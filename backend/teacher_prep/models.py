from __future__ import annotations

import math
from typing import Annotated, Literal, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=12000)]
Minutes = Annotated[float, Field(gt=0, le=480, allow_inf_nan=False)]
CourseType = Literal["subject", "theme"]
Purpose = Literal["same_lesson", "remediation", "next_lesson"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class LessonRequest(Model):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    category: Annotated[str, Field(max_length=40)] = ""
    course_type: CourseType
    grade: Annotated[str, Field(min_length=1, max_length=30)]
    subject: str = ""
    duration_minutes: Minutes
    class_label: Text
    class_id: Identifier | None = None
    class_size: int | None = Field(default=None, gt=0, le=1000)
    prior_knowledge: str | None = None
    difficulties: str | None = None
    constraints: list[Text] = Field(default_factory=list)
    source_ids: list[Identifier] = Field(default_factory=list)
    knowledge_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def subject_required(self):
        if self.course_type == "subject" and not self.subject:
            raise ValueError("学科课需要填写 subject；未提供学情可保留为 null")
        return self


class Goal(Model):
    id: Identifier
    description: Text


class KnowledgePoint(Model):
    id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=48)]
    description: Text
    is_new: bool
    difficulty: Literal["basic", "intermediate", "complex"]


class Activity(Model):
    id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=48)]
    kind: Literal["introduction", "explanation", "practice", "discussion", "assessment", "reflection"]
    goal_ids: list[Identifier] = Field(min_length=1)
    knowledge_ids: list[Identifier] = Field(default_factory=list)
    introduces: list[Identifier] = Field(default_factory=list)
    duration_minutes: Minutes
    thinking_minutes: float = Field(default=0, ge=0, allow_inf_nan=False)
    reasoning_steps: int = Field(default=0, ge=0, le=100)
    instructions: Text

    @model_validator(mode="after")
    def timing(self):
        if self.thinking_minutes > self.duration_minutes:
            raise ValueError("思考练习时间不能超过对应环节时长")
        if not set(self.introduces) <= set(self.knowledge_ids):
            raise ValueError("introduces 必须属于环节 knowledge_ids")
        return self


class Visual(Model):
    kind: Literal["steps", "comparison", "concepts"]
    labels: list[Annotated[str, Field(min_length=1, max_length=26)]] = Field(min_length=2, max_length=4)


class Slide(Model):
    id: Identifier
    activity_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=64)]
    points: list[Annotated[str, Field(min_length=1, max_length=110)]] = Field(
        min_length=1, max_length=12,
        description="一个逻辑教学页的要点，优先1—5条；完整例题可保留6—12条，导出时自动拆成每张最多5条的连续PPT页，共用原讲稿。超过12条须拆为多个逻辑页。")
    teacher_script: Annotated[str, Field(min_length=12, max_length=5000)]
    question_ids: list[Identifier] = Field(default_factory=list)
    visual: Visual | None = None


class Question(Model):
    id: Identifier
    activity_id: Identifier
    goal_ids: list[Identifier] = Field(min_length=1)
    knowledge_ids: list[Identifier] = Field(default_factory=list)
    prompt: Text
    answer: Text
    explanation: Text
    timing: Literal["in_class", "after_class", "delayed"]
    mode: Literal["objective", "rubric"]
    criteria: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def rubric(self):
        if self.mode == "rubric" and not self.criteria:
            raise ValueError("情境判断或理由表达题需要 criteria 评价要点")
        return self


def unique(values: list[str], label: str):
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 含重复标识")


class LessonDesign(Model):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    course_type: CourseType
    grade: Text
    subject: str = ""
    duration_minutes: Minutes
    goals: list[Goal] = Field(min_length=1, max_length=20)
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list, max_length=80)
    activities: list[Activity] = Field(min_length=1, max_length=30)
    slides: list[Slide] = Field(min_length=1, max_length=80)
    questions: list[Question] = Field(min_length=1, max_length=100)
    board_design: Text
    source_ids: list[Identifier] = Field(default_factory=list)
    knowledge_ids: list[Identifier] = Field(default_factory=list)
    context_notes: str = ""

    @model_validator(mode="after")
    def consistency(self):
        groups = {name: getattr(self, name) for name in ("goals", "knowledge_points", "activities", "slides", "questions")}
        for name, values in groups.items():
            unique([value.id for value in values], name)
        all_ids = [value.id for values in groups.values() for value in values]
        unique(all_ids, "教学设计全局ID")
        goal_ids = {g.id for g in self.goals}
        knowledge = {k.id: k for k in self.knowledge_points}
        activities = {a.id: a for a in self.activities}
        questions = {q.id: q for q in self.questions}
        introduced = []
        for activity in self.activities:
            if not set(activity.goal_ids) <= goal_ids or not set(activity.knowledge_ids) <= knowledge.keys():
                raise ValueError(f"环节 {activity.id} 引用了不存在的目标或知识点")
            for identifier in activity.introduces:
                if not knowledge[identifier].is_new:
                    raise ValueError("已学知识不能记为新增知识点")
            introduced.extend(activity.introduces)
        unique(introduced, "新增知识点引入位置")
        expected_new={k.id for k in self.knowledge_points if k.is_new}
        if set(introduced) != expected_new:
            missing=sorted(expected_new-set(introduced));unexpected=sorted(set(introduced)-expected_new)
            details=[]
            if missing:details.append('缺少首次引入：'+', '.join(missing))
            if unexpected:details.append('错误标为首次引入：'+', '.join(unexpected))
            raise ValueError("每个新增知识点必须且只能在一个环节首次引入；"+'；'.join(details))
        if not math.isclose(sum(a.duration_minutes for a in self.activities), self.duration_minutes, abs_tol=0.05):
            raise ValueError("各环节计划用时之和必须等于课程时长（容差0.05分钟）")
        activity_order = {a.id: index for index, a in enumerate(self.activities)}
        if [activity_order.get(s.activity_id, -1) for s in self.slides] != sorted(activity_order.get(s.activity_id, -1) for s in self.slides):
            raise ValueError("课件页面必须按教学环节顺序排列")
        for slide in self.slides:
            if slide.activity_id not in activities or not set(slide.question_ids) <= questions.keys():
                raise ValueError(f"页面 {slide.id} 引用了不存在的环节或题目")
            if any(questions[q].activity_id != slide.activity_id for q in slide.question_ids):
                raise ValueError("页面上的题目必须关联同一教学环节")
        if {s.activity_id for s in self.slides} != set(activities):
            raise ValueError("每个教学环节至少需要一页对应课件")
        for question in self.questions:
            if question.activity_id not in activities or not set(question.goal_ids) <= goal_ids or not set(question.knowledge_ids) <= knowledge.keys():
                raise ValueError(f"题目 {question.id} 的关联无效")
        if set().union(*(set(q.goal_ids) for q in self.questions)) != goal_ids:
            raise ValueError("每个教学目标至少需要一个对应检测题或评价任务")
        return self


class Participation(Model):
    activity_id: Identifier
    participating: int | None = Field(default=None, ge=0)
    eligible: int | None = Field(default=None, gt=0)
    basis: Literal["observed", "estimated"]
    notes: str = ""

    @model_validator(mode="after")
    def denominator(self):
        if self.participating is not None and self.eligible is not None and self.participating > self.eligible:
            raise ValueError("参与人数不能超过应参与人数；填写去重人数而非发言次数")
        return self


class Timing(Model):
    activity_id: Identifier
    actual_minutes: Minutes
    basis: Literal["observed", "estimated"]


class AssessmentResult(Model):
    question_id: Identifier
    correct: int | None = Field(default=None, ge=0)
    met_criteria: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)
    independent: bool | None = None
    timing: Literal["in_class", "after_class", "delayed"]
    days_after: int | None = Field(default=None, ge=0, le=3650)
    basis: Literal["observed", "estimated"]
    errors: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def denominator(self):
        if self.correct is not None and self.met_criteria is not None:
            raise ValueError("正确人数和达到评价要点人数不能混填")
        count = self.correct if self.correct is not None else self.met_criteria
        if count is not None and self.total is not None and count > self.total:
            raise ValueError("正确或达到要点的人数不能超过作答人数")
        if self.timing == "delayed" and self.days_after is None:
            raise ValueError("延迟复测需记录距授课天数")
        return self


class Observation(Model):
    id: Identifier
    activity_id: Identifier
    text: Text
    basis: Literal["observed", "estimated"]


class Feedback(Model):
    raw_text: Text
    observed_at: Text
    requested_changes: str = Field(default='',max_length=12000)
    next_lesson_focus: str = Field(default='',max_length=12000)
    supersedes_feedback_id: Identifier | None = None
    record_mode: Literal['future_teaching', 'revision'] = 'revision'
    participation: list[Participation] = Field(default_factory=list)
    timings: list[Timing] = Field(default_factory=list)
    assessments: list[AssessmentResult] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    teacher_keep: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_records(self):
        unique([p.activity_id for p in self.participation], "参与记录")
        unique([t.activity_id for t in self.timings], "实际用时记录")
        unique([o.id for o in self.observations], "观察记录")
        unique([(a.question_id, a.timing, a.days_after, a.independent, a.basis) for a in self.assessments], "检测记录")
        return self


class Choice(Model):
    suggestion_id: Identifier
    action: Literal["accept", "modify", "reject"]
    instructions: str = ""


class TeacherDecision(Model):
    teacher_confirmed: bool
    purpose: Purpose
    choices: list[Choice] = Field(min_length=1)
    keep_requirements: list[Text] = Field(default_factory=list)
    protected_ids: list[Text] = Field(default_factory=list)
    allow_goal_changes: bool = False
    allow_duration_change: bool = False

    @model_validator(mode="after")
    def confirmed(self):
        if not self.teacher_confirmed:
            raise ValueError("修订需要记录教师已作出的调整选择")
        unique([c.suggestion_id for c in self.choices], "教师选择")
        if any(c.action == "modify" and not c.instructions for c in self.choices):
            raise ValueError("修改建议时需要填写教师的调整要求")
        return self


class ChangeLink(Model):
    target: Text
    suggestion_ids: list[Identifier] = Field(min_length=1)
    explanation: Text


class Revision(Model):
    design: LessonDesign
    summary: Text
    change_links: list[ChangeLink] = Field(min_length=1)


class RevisionOperation(Model):
    op: Literal['update', 'add', 'remove', 'reorder']
    target: Text = Field(description='实体如 slide:S01、activity:A3、question:Q2；课程字段如 lesson:board_design；排序如 sequence:slides')
    fields: dict[str, Any] = Field(default_factory=dict, description='update仅提交改变的字段；add提交新实体字段；remove为空；reorder提交ids数组。')
    after_id: Identifier | None = Field(default=None, description='仅add使用：插入在同组已有ID之后；null追加到末尾。')


class RevisionEdits(Model):
    parent_version_id: Identifier
    operations: list[RevisionOperation] = Field(min_length=1, max_length=160)


class IncrementalRevision(RevisionEdits):
    summary: Text
    change_links: list[ChangeLink] = Field(min_length=1)


SCHEMAS = {"request": LessonRequest, "design": LessonDesign, "feedback": Feedback, "decision": TeacherDecision,
           "revision": Revision, 'revision_patch': IncrementalRevision, 'revision_edits': RevisionEdits}
