"""Deterministic field edits over an immutable saved teaching design."""
from copy import deepcopy
from .analysis import design_diff
from .models import LessonDesign, RevisionEdits, Goal, KnowledgePoint, Activity, Slide, Question

GROUPS={'goal':'goals','knowledge':'knowledge_points','activity':'activities','slide':'slides','question':'questions'}
TYPES={'goal':Goal,'knowledge':KnowledgePoint,'activity':Activity,'slide':Slide,'question':Question}


def split_target(target):
    kind,separator,identifier=target.partition(':')
    if not separator or not identifier:raise ValueError('修改标识必须为 类型:ID')
    return kind,identifier


def outline(raw):
    design=LessonDesign.model_validate(raw)
    result=design.model_dump(mode='json')
    result['knowledge_points']=[{k:row[k] for k in ('id','title','is_new','difficulty')} for row in result['knowledge_points']]
    result['activities']=[{k:v for k,v in row.items() if k!='instructions'} for row in result['activities']]
    result['slides']=[{k:row[k] for k in ('id','activity_id','title','question_ids')} for row in result['slides']]
    result['questions']=[{k:row[k] for k in ('id','activity_id','goal_ids','knowledge_ids','prompt','timing','mode')} for row in result['questions']]
    result.pop('board_design')
    result['detail_access']='此处是索引；讲稿、正文、活动指令、答案与板书未重复展开。修改已有实体前先用teacher_read_revision_targets读取原文。'
    return result


def read_targets(raw,targets):
    if not isinstance(targets,list) or not 1<=len(targets)<=8 or len(set(targets))!=len(targets):
        raise ValueError('每次读取1—8个不同的教学实体标识')
    design=LessonDesign.model_validate(raw).model_dump(mode='json')
    result=[];schemas=set()
    for target in targets:
        kind,identifier=split_target(target)
        if kind in GROUPS:
            entity=next((v for v in design[GROUPS[kind]] if v['id']==identifier),None)
            if entity is None:raise ValueError('原设计中不存在 '+target)
            row={'target':target,'content':entity}
            if kind not in schemas:row['field_schema']=TYPES[kind].model_json_schema();schemas.add(kind)
            result.append(row)
        elif kind=='lesson' and identifier in LessonDesign.model_fields and identifier not in GROUPS.values():
            result.append({'target':target,'content':{identifier:design[identifier]}})
        elif kind=='sequence' and identifier in GROUPS.values():
            result.append({'target':target,'content':{'ids':[v['id'] for v in design[identifier]]}})
        else:raise ValueError('不支持的教学实体标识 '+target)
    return result


def merge_edits(raw,patch,parent_id):
    edits=RevisionEdits.model_validate(patch)
    if edits.parent_version_id!=parent_id:raise ValueError('增量修订的原版本标识不一致')
    design=deepcopy(raw)
    targets=[op.target for op in edits.operations]
    if len(set(targets))!=len(targets):raise ValueError('同一修改标识只能出现一次；请合并改变的字段')
    for operation in edits.operations:
        kind,identifier=split_target(operation.target)
        fields=operation.fields
        if operation.op!='add' and operation.after_id is not None:raise ValueError('after_id仅用于新增实体')
        if operation.op=='reorder':
            if kind!='sequence' or identifier not in GROUPS.values() or set(fields)!={'ids'}:
                raise ValueError('排序需要sequence:集合名和fields.ids')
            ids=fields['ids'];current={v['id']:v for v in design[identifier]}
            if not isinstance(ids,list) or len(ids)!=len(current) or len(set(ids))!=len(ids) or set(ids)!=set(current):
                raise ValueError('排序必须恰好包含当前集合的全部ID，不得丢失或重复')
            design[identifier]=[current[id] for id in ids];continue
        if kind=='lesson':
            if operation.op!='update' or identifier not in LessonDesign.model_fields or identifier in GROUPS.values() or set(fields)!={identifier}:
                raise ValueError('课程字段仅允许update，并提交与target同名的单个字段')
            design[identifier]=fields[identifier];continue
        if kind not in GROUPS:raise ValueError('不支持的修改标识 '+operation.target)
        items=design[GROUPS[kind]];index=next((i for i,v in enumerate(items) if v['id']==identifier),None)
        if operation.op=='add':
            if index is not None:raise ValueError('新增实体ID已存在 '+operation.target)
            if fields.get('id',identifier)!=identifier:raise ValueError('新增实体ID与target不一致')
            entity={**deepcopy(fields),'id':identifier}
            if operation.after_id is None:items.append(entity)
            else:
                position=next((i for i,v in enumerate(items) if v['id']==operation.after_id),None)
                if position is None:raise ValueError('插入位置不属于同一实体集合')
                items.insert(position+1,entity)
        elif operation.op=='update':
            if index is None:raise ValueError('不能修改不存在的实体 '+operation.target)
            if not fields or 'id' in fields:raise ValueError('update仅提交实际修改字段，不得更改ID')
            items[index]={**items[index],**deepcopy(fields)}
        elif operation.op=='remove':
            if index is None or fields:raise ValueError('remove需要已有实体且fields为空')
            items.pop(index)
        else:raise ValueError('该操作不能用于实体')
    # Cross references, pagination limits, goal coverage and lesson timing are validated after merging.
    return LessonDesign.model_validate(design)


def compact_diff(before,after):
    changes=design_diff(LessonDesign.model_validate(before),after)
    if not changes:raise ValueError('未发生实际修改，不建立重复版本')
    return {'changes':[{'target':row['target'],'fields':sorted(k for k in set(row['before'] or {})|set(row['after'] or {})
                       if (row['before'] or {}).get(k)!=(row['after'] or {}).get(k))
                       if isinstance(row['before'],dict) or isinstance(row['after'],dict) else []} for row in changes]}
