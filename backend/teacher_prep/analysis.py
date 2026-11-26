from __future__ import annotations

from .models import Feedback, LessonDesign, TeacherDecision
from .feedback_summary import propose_from_text
from .learning_records import summarize_record


def fraction(numerator, denominator):
    return None if numerator is None or denominator is None or denominator <= 0 else round(numerator / denominator, 4)


def analyze(design: LessonDesign, feedback: Feedback | None = None) -> dict:
    timings = {t.activity_id: t for t in feedback.timings} if feedback else {}
    knowledge = {k.id: k for k in design.knowledge_points}
    density = []
    for activity in design.activities:
        actual = timings.get(activity.id)
        density.append({
            "activity_id": activity.id, "title": activity.title, "kind": activity.kind,
            "introduced_knowledge_ids": activity.introduces,
            "new_knowledge_count": len(activity.introduces),
            "difficulties": {identifier: knowledge[identifier].difficulty for identifier in activity.knowledge_ids},
            "reasoning_steps": activity.reasoning_steps,
            "planned_minutes": activity.duration_minutes,
            "planned_thinking_minutes": activity.thinking_minutes,
            "planned_points_per_activity_minute": fraction(len(activity.introduces), activity.duration_minutes),
            "actual_minutes": actual.actual_minutes if actual else None,
            "actual_time_basis": actual.basis if actual else None,
            "actual_points_per_activity_minute": fraction(len(activity.introduces), actual.actual_minutes) if actual else None,
            "interpretation": "以整个环节分钟为分母；按环节类型查看，不等同认知负荷或教学质量。难度和单位划分由教师核对。",
        })
    participation = []
    assessments = []
    suggestions = []

    def suggest(identifier, title, evidence_ids, observation, hypothesis, actions, verify):
        suggestions.append({"id": identifier, "title": title, "evidence_ids": evidence_ids,
                            "observation": observation, "hypothesis": hypothesis,
                            "suggested_actions": actions, "verify_next_time": verify,
                            "status": "awaiting_teacher_choice"})

    if feedback:
        for index, item in enumerate(feedback.participation, 1):
            evidence = f"participation:{index}"
            participation.append({**item.model_dump(), "participation_fraction": fraction(item.participating, item.eligible), "evidence_id": evidence})
            if item.participating is not None and item.eligible is not None and item.participating < item.eligible:
                suggest(f"participation_{index}", "核对参与机会与观察范围", [evidence],
                        f"{item.activity_id} 记录参与人数 {item.participating}/{item.eligible}；依据为{item.basis}。",
                        "未观察到参与不等于没有学习，需核对参与机会和回答方式。",
                        ["考虑先独立写下答案，再进行同伴解释或抽样汇报", "保留原有有效活动，按教师选择调整参与方式"],
                        "记录下一次同环节的去重参与人数，并检查学生答案质量。")
        questions = {q.id: q for q in design.questions}
        for index, item in enumerate(feedback.assessments, 1):
            evidence = f"assessment:{index}"
            rubric = questions[item.question_id].mode == "rubric"
            count = item.met_criteria if rubric else item.correct
            measure = "达到评价要点" if rubric else "正确"
            assessments.append({**item.model_dump(), "correct_fraction": None if rubric else fraction(count, item.total),
                                "met_criteria_fraction": fraction(count, item.total) if rubric else None,
                                "criteria": questions[item.question_id].criteria,
                                "goal_ids": questions[item.question_id].goal_ids, "evidence_id": evidence,
                                "interpretation": "仅描述本题及本次条件；即时结果不代表长期掌握，估计值不是实测值。"})
            if count is not None and item.total is not None and count < item.total:
                if item.independent is True:
                    hypothesis = "可能涉及概念辨析、前置知识或应用步骤，需结合具体作答核对，不能直接归因为信息密度过高。"
                    actions = ["围绕典型错误补充一个对比或分步示例", "安排同目标的新题进行独立练习与讲评", "同步调整其他环节时间以保持总课时"]
                else:
                    hypothesis = "独立完成条件未确认；当前结果不足以判断独立掌握。"
                    actions = ["先安排同目标、难度接近的独立检测", "由教师结合具体作答决定是否补讲，不直接降低教学目标"]
                suggest(f"assessment_{index}", f"检查 {item.question_id} 对应目标", [evidence],
                        f"{item.question_id} {measure}人数为 {count}/{item.total}；检测时点 {item.timing}，依据 {item.basis}。",
                        hypothesis, actions, "采用同目标新题，分别记录即时与延迟结果、独立完成条件和错误类型。")
        for index, item in enumerate(feedback.timings, 1):
            activity = next(a for a in design.activities if a.id == item.activity_id)
            if item.actual_minutes > activity.duration_minutes:
                suggest(f"timing_{index}", "重新分配环节时间", [f"timing:{index}"],
                        f"{item.activity_id} 计划 {activity.duration_minutes:g} 分钟，记录实际 {item.actual_minutes:g} 分钟（{item.basis}）。",
                        "可能与难度、活动组织或时间预估有关，尚不能确定原因。",
                        ["核对超时发生在哪个步骤", "按教师取舍拆分任务或压缩非核心环节，保证总课时"],
                        "下次记录同环节实际用时及任务完成情况。")
        for item in feedback.observations:
            suggest(f"observation_{item.id}", "依据教师观察调整", [f"observation:{item.id}"], item.text,
                    "这是教师提供的观察或估计，具体原因需要教师判断及作答证据。",
                    ["由教师选择需要保留和调整的环节", "针对该观察补充解释、例子或诊断题"],
                    "为该问题安排对应观察或检测，避免只依据课堂气氛判断。")
    if not feedback or not feedback.assessments:
        suggest("collect_evidence", "补充与目标对应的学习证据", [], "尚未提供课后或课堂检测结果。",
                "目前不能判断学生掌握程度。", ["选用已关联教学目标的检测题或情境任务，记录结果与时间"],
                "学科课核对作答及错因；主题课核对情境判断及表达理由。")
    # Describe density alongside evidence at the same activity. No universal
    # threshold, weighting, classroom score, or causal attribution is assumed.
    activity_evidence = []
    questions = {q.id: q for q in design.questions}
    for row in density:
        related_participation = [p for p in participation if p["activity_id"] == row["activity_id"]]
        related_results = [r for r in assessments if questions[r["question_id"]].activity_id == row["activity_id"]]
        evidence_ids = [p["evidence_id"] for p in related_participation] + [r["evidence_id"] for r in related_results]
        timing_entry = next((index for index, t in enumerate(feedback.timings, 1) if t.activity_id == row["activity_id"]), None) if feedback else None
        if timing_entry is not None:
            evidence_ids.append(f"timing:{timing_entry}")
        context = {"activity_id": row["activity_id"], "new_knowledge_count": row["new_knowledge_count"],
                   "planned_density": row["planned_points_per_activity_minute"],
                   "actual_density": row["actual_points_per_activity_minute"],
                   "planned_thinking_minutes": row["planned_thinking_minutes"],
                   "reasoning_steps": row["reasoning_steps"], "evidence_ids": evidence_ids,
                   "interpretation": "同一环节并列查看计划、实际用时、参与和检测；不同题目和完成条件不能直接比较，不能据此确定密度导致学习结果。"}
        activity_evidence.append(context)
        # Suggest examining pacing only where the teacher supplied an observed
        # overrun and there is new material, not from a made-up density cutoff.
        if row["new_knowledge_count"] and row["actual_minutes"] is not None and row["actual_minutes"] > row["planned_minutes"]:
            suggest(f"density_{row['activity_id']}", "结合实际用时检查新内容安排", evidence_ids,
                    f"{row['title']} 首次引入 {row['new_knowledge_count']} 个知识点；计划密度 {row['planned_points_per_activity_minute']}，按记录用时计算为 {row['actual_points_per_activity_minute']} 点/分钟。",
                    "同样的新内容用了更多时间，可能与知识难度、组织方式或理解过程有关；较低的实际密度本身不是效果提升的证据。",
                    ["核对卡住的具体知识点和推理步骤，考虑将一个示例分步展开", "在总课时内重新分配讲解与独立练习时间，并保留有效活动"],
                    "下次同时记录环节实际用时、独立完成的同目标新题与延迟复测，核对调整效果。")
            suggestions[-1]["density_context"] = context
    qualitative_summary=None
    if feedback and feedback.record_mode == 'future_teaching':
        suggestions=[]
        qualitative_summary={'method':'local_teacher_record','status':'record_only','purpose':'future_teaching','measurement_inference':False}
    elif feedback:
        proposal,qualitative_summary=propose_from_text(design,feedback,suggestions)
        if proposal:suggestions.insert(0,proposal)
    return {"metric_version": "0.2.0", "density": density, "participation": participation,
            "activity_evidence": activity_evidence,
            "assessment_results": assessments, "suggestions": suggestions,
            "overall_score": None, "causal_conclusion": None,'feedback_summary':qualitative_summary,
            'learning_record':summarize_record(design,feedback) if feedback else None,
            "missing": [name for name, missing in {"actual_timing": not timings, "participation": not participation, "assessment": not assessments}.items() if missing],
            "teacher_keep": feedback.teacher_keep if feedback else []}


def design_diff(before: LessonDesign, after: LessonDesign) -> list[dict]:
    differences = []
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    groups = {"goals": "goal", "knowledge_points": "knowledge", "activities": "activity", "slides": "slide", "questions": "question"}
    for key, label in groups.items():
        old_map = {v["id"]: v for v in old[key]}
        new_map = {v["id"]: v for v in new[key]}
        for identifier in sorted(old_map.keys() | new_map.keys()):
            if old_map.get(identifier) != new_map.get(identifier):
                differences.append({"target": f"{label}:{identifier}", "before": old_map.get(identifier), "after": new_map.get(identifier)})
        if set(old_map) == set(new_map) and [v["id"] for v in old[key]] != [v["id"] for v in new[key]]:
            differences.append({"target": f"sequence:{key}", "before": [v["id"] for v in old[key]], "after": [v["id"] for v in new[key]]})
    for key in old.keys() - groups.keys():
        if old[key] != new[key]:
            differences.append({"target": f"lesson:{key}", "before": old[key], "after": new[key]})
    return sorted(differences, key=lambda entry: entry["target"])


def validate_revision(before, after, decision: TeacherDecision, links, analysis):
    if after.course_type != before.course_type or after.grade != before.grade or after.subject != before.subject:
        raise ValueError("修订不能隐式改变课程类型、年级或学科；另建课程处理")
    if not decision.allow_goal_changes and before.goals != after.goals:
        raise ValueError("教师未允许修改核心教学目标")
    if not decision.allow_duration_change and before.duration_minutes != after.duration_minutes:
        raise ValueError("教师未允许改变总课时；新增环节时需要同步分配时间")
    if decision.purpose == "same_lesson" and set(before.source_ids) - set(after.source_ids):
        raise ValueError("同课优化不能无提示移除原教材依据")
    changes = design_diff(before, after)
    if not changes:
        raise ValueError("未发生实际修改，不建立重复版本")
    accepted = {c.suggestion_id for c in decision.choices if c.action in {"accept", "modify"}}
    if not accepted:
        raise ValueError("没有已采纳或修改的建议，不执行修订")
    suggestion_map = {s["id"]: s for s in analysis["suggestions"]}
    link_map = {link.target: link for link in links}
    if len(link_map) != len(links) or set(link_map) != {c["target"] for c in changes}:
        raise ValueError("change_links 必须逐项覆盖实际差异，不能省略或虚报修改")
    for change in changes:
        if change["target"] in decision.protected_ids:
            raise ValueError(f"修改了教师要求保护的内容：{change['target']}")
        link = link_map[change["target"]]
        if not set(link.suggestion_ids) <= accepted:
            raise ValueError("修改关联了未采纳或已拒绝的建议")
        change["explanation"] = link.explanation
        change["suggestion_ids"] = link.suggestion_ids
        change["evidence_ids"] = sorted({e for s in link.suggestion_ids for e in suggestion_map[s]["evidence_ids"]})
        change["evidence_summary"] = [suggestion_map[s]["observation"] for s in link.suggestion_ids]
        change["verify_next_time"] = [suggestion_map[s]["verify_next_time"] for s in link.suggestion_ids]
    return changes
