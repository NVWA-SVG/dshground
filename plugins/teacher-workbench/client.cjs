window.__ModuleLoader__.load({id:'dsh-teacher-workbench',factory:(require)=>{
const exports={};
const React = require('react');
exports.inject = ['slots', 'layout'];
exports.apply = function(ctx) {
  ctx.slots.inject('main', () => ctx.slots.register({name:'main', key:'teacher-workbench'},
    () => React.createElement('iframe', {src:'/teacher/', title:'教师工作台', style:{border:0,width:'100%',height:'100%',background:'#f5f7fb'}})));
  ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({name:'sidebar.panellist', id:'teacher-workbench', order:0,label:()=> '教师工作台'},
    () => React.createElement('span', {'aria-hidden':true}, '教')));
};
return exports;
}});
