// Session metadata records the requested preset; DSH's programmatic factory
// does not mount it. Bind the preset before installing task-scoped tools.
export async function mountTeacherAgent(ctx,agentCtx){
  if(typeof ctx.agentPresets?.mount!=='function')throw new Error('教师提示词服务尚未就绪，任务未发送到模型。');
  const preset=await ctx.agentPresets.mount(agentCtx,'teacher');
  if(preset?.id!=='teacher')throw new Error('教师提示词未正确加载，任务未发送到模型。');
}
