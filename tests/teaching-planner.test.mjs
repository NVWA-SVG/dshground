import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createPptService} from '../plugins/teacher-workbench/ppt-service.mjs';

test('a new visual task rejects generic page plans and accepts explicit teaching tasks before composing',async()=>{
  const root=await mkdtemp(path.resolve('.test-runs/teaching-agent-'));await mkdir(path.join(root,'config'));
  await writeFile(path.join(root,'config/presentation-api.json'),JSON.stringify({provider:'native',asset_mode:'local'}));
  await writeFile(path.join(root,'config/image-generation.json'),JSON.stringify({enabled:false}));
  let saves=0,composes=0;
  const ctx={credentials:{resolve:async()=>{throw new Error('No credential should be used');}},agentPresets:{mount:async(_ctx,id)=>({id})},agents:{create:async options=>{
    const registered=new Map();await options.setup({tools:{restrict:()=>{},register:tool=>registered.set(tool.name,tool)}});
    return {agent:{id:'mock_visual',followup:message=>assert.ok(message.content[0].text.includes('diagram_candidates')),cancel:()=>{},whenIdle:async()=>{
      const tool=registered.get('teacher_save_ppt_prompt'),base={page_number:1,layout:'宽文字',layout_kind:'text',visual_prompt:'依据原任务观察'};
      const rejected=JSON.parse(await tool.execute({plan:{style:'清晰课堂',pages:[base]}},{}));assert.equal(rejected.ok,false);assert.match(rejected.error,/teaching_goal/);assert.equal(saves,0);
      const accepted=JSON.parse(await tool.execute({plan:{style:'清晰课堂',pages:[{...base,teaching_goal:'比较原有对象',student_task:'比较并说明你的判断。',learning_focus:'原对象的相同点和不同点',asset_reason:'本页用文字组织比较任务',density_note:'保留比较与解释的思考空间'}]}},{}));assert.equal(accepted.ok,true,accepted.error);
    }},dispose:async()=>{}};
  }}};
  const call=async(op)=>{
    if(op==='ppt-prepare')return {brief:{planning_version:2,version_id:'version_mock',pages:[{number:1,title:'观察比较',points:['比较两个对象'],diagram_candidates:[],teaching_context:{activity_minutes:5,activity_new_knowledge_count:0}}]},schema:{type:'object'},assets:[]};
    if(op==='ppt-save-prompt'){saves++;return {saved:true};}
    if(op==='ppt-compose-local'){composes++;return {valid:true};}throw new Error('Unexpected '+op);
  };
  const service=createPptService({root,ctx,call,pluginDir:path.resolve('plugins/teacher-workbench'),persist:async()=>{},handles:new Map()});
  await service.execute({id:'task_teaching'},'version_mock');assert.equal(saves,1);assert.equal(composes,1);
});
