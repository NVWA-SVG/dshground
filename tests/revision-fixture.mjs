// Adapt existing complete fixture files to the model's field-only revision contract.
export function fixturePatch(before,revision,parent_version_id){
  const operations=[],groups={goals:'goal',knowledge_points:'knowledge',activities:'activity',slides:'slide',questions:'question'};
  for(const [name,kind] of Object.entries(groups)){
    const old=new Map(before[name].map(row=>[row.id,row])),after=revision.design[name];
    for(const row of after){
      const previous=old.get(row.id);
      if(!previous)operations.push({op:'add',target:kind+':'+row.id,fields:row,after_id:after[after.indexOf(row)-1]?.id||null});
      else{
        const fields=Object.fromEntries(Object.entries(row).filter(([key,value])=>key!=='id'&&JSON.stringify(previous[key])!==JSON.stringify(value)));
        if(Object.keys(fields).length)operations.push({op:'update',target:kind+':'+row.id,fields});
      }
    }
    for(const id of old.keys())if(!after.some(row=>row.id===id))operations.push({op:'remove',target:kind+':'+id,fields:{}});
    if(old.size===after.length&&after.every(row=>old.has(row.id))&&[...old.keys()].join()!==after.map(row=>row.id).join())operations.push({op:'reorder',target:'sequence:'+name,fields:{ids:after.map(row=>row.id)}});
  }
  for(const [key,value] of Object.entries(revision.design))if(!groups[key]&&JSON.stringify(before[key])!==JSON.stringify(value))operations.push({op:'update',target:'lesson:'+key,fields:{[key]:value}});
  return {parent_version_id,operations,summary:revision.summary,change_links:revision.change_links};
}
export async function readPatchTargets(tools,patch){
  const targets=patch.operations.filter(op=>['update','remove'].includes(op.op)).map(op=>op.target);
  for(let i=0;i<targets.length;i+=8){const result=JSON.parse(await tools.get('teacher_read_revision_targets').execute({targets:targets.slice(i,i+8)},{}));if(result.error)throw new Error(result.error);}
}
