"""Write explicitly synthetic engineering fixtures, not model-generated or classroom evidence."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from teacher_prep.models import LessonDesign
from teacher_prep.analysis import design_diff


def lesson(kind):
    subject = kind == "subject"
    title = "认识几分之一" if subject else "意见不同时怎样沟通"
    grade = "三年级" if subject else "四年级"
    goals = [
        {"id": "G1", "description": "能识别平均分并说明每份同样大的依据" if subject else "能在冲突情境中区分可观察的事实与个人感受"},
        {"id": "G2", "description": "能用几分之一表示一个整体平均分后的其中一份" if subject else "能使用倾听复述和具体请求表达不同意见"},
        {"id": "G3", "description": "能说明分母表示平均分的份数，并辨析没有平均分的反例" if subject else "能提出兼顾双方需要的协商办法并说明理由"},
    ]
    knowledge = [
        {"id": "K1", "title": "平均分" if subject else "识别感受", "description": "每份同样多或同样大" if subject else "用具体词描述自己的感受，不替他人下结论", "is_new": False, "difficulty": "basic"},
        {"id": "K2", "title": "一个整体" if subject else "事实与评价", "description": "先明确被平均分的是哪一个整体" if subject else "说出具体发生的事，避免用总是或从来不概括他人", "is_new": True, "difficulty": "basic"},
        {"id": "K3", "title": "几分之一" if subject else "倾听与复述", "description": "把整体平均分成几份，其中一份就是它的几分之一" if subject else "先复述对方的意思，再确认自己是否理解准确", "is_new": True, "difficulty": "intermediate"},
        {"id": "K4", "title": "分母的含义" if subject else "提出具体请求", "description": "分母表示整体平均分的份数，不能只数看见的块数" if subject else "将希望改变的行为说清楚，并邀请对方一起商量", "is_new": True, "difficulty": "intermediate"},
    ]
    names = ["分一分的经验", "先确定一个整体", "认识几分之一", "用反例检查理解", "独立检测与讲评", "整理与课后跟进"] if subject else ["看见不同意见", "分清事实和感受", "先倾听再复述", "两人情境练习", "选择与说明理由", "整理与行动约定"]
    durations = [5, 8, 8, 8, 7, 4] if subject else [5, 6, 8, 10, 7, 4]
    kinds = ["introduction", "explanation", "explanation", "practice", "assessment", "reflection"] if subject else ["introduction", "explanation", "explanation", "discussion", "assessment", "reflection"]
    instructions = [
        "让学生用纸片演示两人分享，先说出怎样判断每份同样大。" if subject else "呈现两位同学都想先使用同一盒彩笔的情境，请学生描述看到的事情。",
        "展示同一个圆片的完整图和等分图，用手指圈出被分的整体。" if subject else "比较具体发生的事情和对人的评价，让学生改写一句带有总是的表达。",
        "把同一个整体平均分成四份，逐步说出其中一份的含义、读法与写法。" if subject else "教师示范先复述同伴的意思，再确认是否理解，不急于反驳。",
        "比较平均分与不平均分的四块图，学生先独立判断，再解释理由。" if subject else "两人交换扮演角色，先说事实和感受，再复述并提出具体请求。",
        "使用 Q1 和 Q2 检查整体、等份与分母含义，对典型错误进行讲评。" if subject else "围绕新的冲突情境独立提出办法，依据是否尊重双方和是否具体可行进行讨论。",
        "让学生完成一句话总结，布置课后检测，并说明后续将用新图进行复测。" if subject else "学生写下一项自己愿意尝试的沟通动作，约定后续分享尝试过程。",
    ]
    activities = []
    for index, name in enumerate(names):
        activities.append({"id": f"A{index + 1}", "title": name, "kind": kinds[index],
                           "goal_ids": ["G1"] if index < 2 else ["G2", "G3"],
                           "knowledge_ids": ["K1"] if index == 0 else ["K1", "K2"] if index == 1 else ["K2", "K3"] if index == 2 else ["K2", "K3", "K4"],
                           "introduces": [] if index in [0, 4, 5] else [f"K{index + 1}"],
                           "duration_minutes": durations[index], "thinking_minutes": [2, 2, 3, 6, 4, 1][index],
                           "reasoning_steps": [1, 2, 3, 3, 2, 1][index], "instructions": instructions[index]})
    subject_content = [
        ("怎样分才公平", ["把一张纸分给两个人。", "每一份同样大，才能叫平均分。", "先折一折，再用重合的方法检查。"], "同学们，今天我们从分享一张纸开始。请先想一想，如果两个人都希望分到同样大的纸，可以怎样做？请用折一折的方法试一试，再将两份叠在一起检查。我们关注的是每一份是否同样大，而不是有没有分成两块。"),
        ("说明你的判断", ["这两份能完全重合吗？", "说一说判断平均分的依据。", "块数相同，不一定每份同样大。"], "请先独立判断，然后和同桌交流理由。发言时请说出你看见的依据，比如边能对齐、面积能够重合。只说看起来差不多还不够，我们可以通过折叠进一步检查。对于意见不同的图，我们一起回到每份是否同样大这个条件。"),
        ("先找到一个整体", ["这张完整的圆片是一个整体。", "接下来的每一份，都来自这个整体。", "先指整体，再指其中的一份。"], "请大家先用手指沿着完整圆片的边缘画一圈。我们把这张完整的圆片看作一个整体。后面说它的几分之一时，必须先知道它指的是哪一个整体。请同桌互相指一指，不要只指分出来的小块。"),
        ("整体变了，表达要说清", ["说明你分的是哪一张纸或哪一个图形。", "讨论时把整体和其中一份同时指出来。", "不用孤立的小块直接判断几分之一。"], "这里有两张大小不同的纸。我们分别把它们看作一个整体，观察其中一份。今天先不急着比较小块的大小，而要把分的是哪个整体说清楚。请完整表达：把哪一个整体平均分成几份，指的是其中哪一份。"),
        ("把四份中的一份说清楚", ["把同一个整体平均分成四份。", "其中一份是它的四分之一，写作 1/4。", "四分之一离不开平均分这个条件。"], "现在请看同一个圆片被平均分成了四份。老师只取出其中的一份，这一份就是这个圆片的四分之一，写作一条分数线，上面是1，下面是4。请先用自己的话说完整，再读一读四分之一。我们不能漏掉平均分这个条件。"),
        ("数字分别告诉我们什么", ["下面的4表示整体平均分成四份。", "上面的1表示其中的一份。", "结合图形解释，比只记读法更重要。"], "请先不要背口诀，而是指着图解释。下面的4告诉我们，这个整体平均分成了四份；上面的1表示我们说的是其中的一份。请同桌轮流说明两个数字的意思，听的一方检查对方有没有说出整体和平均分。"),
        ("分成四块就是四分之一吗", ["观察四块是不是同样大。", "没有平均分时，不能只凭块数判断。", "先判断条件，再写分数。"], "请看这张被分成四块的图，其中有的块大，有的块小。虽然看见了四块，但每份并不同样大，所以不能把其中任意一块称为这个整体的四分之一。请先自己写下判断，再用平均分这个条件向同桌解释。"),
        ("用完整句子解释", ["我把这个整体平均分成了……份。", "其中的一份是它的……分之一。", "请指出句子和图形对应的位置。"], "我们用完整句子检查自己的理解。请先指出整个图，再说平均分成了几份，最后指出其中一份。听的同学不要只判断分数读得对不对，还要检查分的份数、平均分条件和所指的小块是否对应。"),
        ("独立完成，再讲理由", ["先完成课堂检测 Q1。", "独立标出整体、等份和其中一份。", "把最不确定的地方圈出来。"], "现在请大家独立完成题目，先不与同桌核对。请把整体圈出来，在图上标出平均分的份数，再写其中一份的分数。做完后圈出自己最不确定的地方，老师会根据这些记录安排讲评，而不只看最后的答案。"),
        ("一起检查典型错误", ["判断错误发生在整体、平均分还是份数。", "用图解释分母的含义。", "修改答案时写出一个理由。"], "我们选取一份匿名作答来讨论。请先找出它判断的依据，而不是只说答案错了。如果没有检查平均分，我们就回到每份是否同样大；如果把分母理解成小块的编号，就请指着整个图重新数一数平均分的份数。"),
        ("今天学会了什么", ["先找整体。", "确认平均分。", "说出其中一份的几分之一。"], "请用三个动作整理今天的方法：先找到一个整体，再确认它被平均分，最后说其中一份是它的几分之一。请每位同学给自己画一个简单图，并用一句话说明。老师会留意大家是否真正用图解释，而不只是重复词语。"),
        ("课后用新图再检查", ["完成课后检测 Q2，并保留解释过程。", "隔几天用 Q3 的新情境复测。", "记录问题，下一次有针对性地练习。"], "课后请完成新的图形题，并写出你这样表示的理由。隔几天我们还会用新的情境检查是否仍能独立解释。遇到困难时，请把自己卡住的位置写下来，老师会根据这些反馈调整后面的例子和练习安排。"),
    ]
    theme_content = [
        ("两个人都想先用彩笔", ["小安正在用彩笔，小林也想马上使用。", "两人说话越来越急。", "先描述发生的事，不急着评判谁。"], "请大家看这段情境：两位同学都想先使用同一盒彩笔。先不要急着给其中一位贴标签，请说出你看见或听见的事情。我们可以观察谁在使用、谁提出了请求，也可以注意他们说话的方式。"),
        ("不同意见可以被说清", ["每个人可能有不同的需要。", "先听见需要，再讨论办法。", "不同意某个办法，不等于否定一个人。"], "同一件事里，每个人的需要可能不同。一位同学想完成眼前的画，另一位担心轮不到自己。我们今天练习的是把不同意见说清楚，让对方愿意听，并一起寻找可行的办法。请先猜想双方可能需要什么，再说明依据。"),
        ("说事实，不用总是概括", ["事实：我刚才问了两次，还没有拿到彩笔。", "评价：你总是只顾自己。", "把能观察到的事情说具体。"], "请比较这两句话。第一句说的是刚才发生的具体事情，我们可以回到情境里核对。第二句用了总是，对整个人作了概括，容易让对方马上反驳。请尝试把一句责备的话改成描述具体事实。"),
        ("感受由自己表达", ["我有些着急，因为担心来不及完成。", "说自己的感受，不替对方判断动机。", "事实和感受可以分开说。"], "表达感受时，可以说我有些着急，而不是认定你就是故意不借给我。我们了解自己的感受，却不一定知道对方的想法。请先说一件具体的事，再用一个合适的词表达自己的感受，同桌听一听有没有把猜测说成事实。"),
        ("先复述，再确认", ["你的意思是，你还需要两分钟完成这一部分？", "我理解得对吗？", "确认以后再表达自己的需要。"], "老师先示范做一个倾听的人。我会先用自己的话复述对方的意思，再问我理解得对吗。复述不是赞同所有要求，而是先确认自己听懂了。请听的同学观察：有没有打断，有没有把对方的意思改成自己想听的意思。"),
        ("把请求说得具体", ["能不能两分钟后轮到我？", "我们可以先分开用几种颜色吗？", "给对方回应和协商的机会。"], "请把你应该对我好一点换成一个具体请求。例如能不能两分钟后轮到我，或者我们能否先分开用几种颜色。请求要让对方知道可以做什么，也留出回应和商量的机会。请每组尝试说出两个不同办法。"),
        ("两人交换角色练习", ["第一轮：说事实、感受和请求。", "倾听者复述并确认。", "第二轮交换角色，再寻找双方可接受的办法。"], "现在两人一组，第一轮由一人表达，另一人练习复述；完成后交换角色。表达时依次说具体事情、自己的感受和请求。请不要急着追求表演热闹，我们更关注每位同学是否都获得完整表达和倾听的机会。"),
        ("观察的是动作和理由", ["是否描述了具体事实？", "是否准确复述了对方的意思？", "提出的办法是否具体、可行并考虑双方？"], "观察同学练习时，我们不评价谁是好孩子，而记录具体的沟通动作。例如有没有用具体事实开头，有没有确认对方的意思，提出的办法是否双方都能尝试。请用一句具体反馈帮助同伴改进。"),
        ("换一个情境独立想", ["小组成员对展示顺序意见不同。", "先写一个可以尝试的办法。", "说明这个办法怎样照顾双方需要。"], "请换到一个新情境：小组成员对展示顺序有不同意见。先独立写一个办法，再说明它怎样回应双方的需要。没有唯一固定的句子，但办法需要具体可行，理由需要与情境有关。先完成自己的想法，再与同伴交流。"),
        ("比较方案，说明理由", ["只让一方让步，可能留下什么问题？", "能否提出一个双方都可以尝试的方案？", "听到新的理由后，可以调整自己的办法。"], "我们比较两种方案时，不只看谁说得响亮，而要看是否听见双方的需要，能否实际执行。请说明某个方案可能遇到的困难，并邀请提出者作调整。听到新的理由后愿意修改，也是这节课要练习的能力。"),
        ("带走一个沟通动作", ["描述具体事实。", "复述确认意思。", "表达请求，一起商量。"], "请回顾今天练习的三个动作，选一个自己最想继续尝试的动作写下来。你可以选择先描述事实，也可以选择先复述对方的意思。请说明准备在什么情境里尝试，不需要许下抽象而很难检查的保证。"),
        ("下次分享尝试的过程", ["记录一次真实尝试，不写同学姓名。", "说明你说了什么、对方怎样回应。", "困难可以成为下一次练习的材料。"], "接下来几天，可以记录一次沟通尝试，只写必要情境，不记录同学姓名。请描述自己具体说了什么，对方怎样回应，哪里仍然有困难。我们下次用这些匿名经历继续讨论办法，而不是按一次表现给同学贴标签。"),
    ]
    slides = []
    for index, (heading, points, script) in enumerate(subject_content if subject else theme_content, 1):
        slide = {"id": f"S{index:02d}", "activity_id": f"A{(index - 1) // 2 + 1}", "title": heading, "points": points, "teacher_script": script, "question_ids": []}
        if index in [3, 6, 11]:
            slide["visual"] = {"kind": "steps", "labels": (["找到一个整体", "确认平均分", "解释其中一份"] if subject else ["描述事实与感受", "复述并确认", "提出具体请求"])}
        slides.append(slide)
    questions = [
        {"id": "Q1", "activity_id": "A5", "goal_ids": ["G1"], "knowledge_ids": ["K1", "K2"],
         "prompt": "一个圆平均分成四份，取出一份。另一圆分成大小不同的四块。哪个图能表示其中一份是原圆的四分之一？说明理由。" if subject else "情境中一位同学说：你总是不让我用。请把这句话改成具体事实与自己的感受。",
         "answer": "只有平均分的圆可以；四份必须同样大。" if subject else "示例：我刚才询问后还没有轮到使用，我有些着急。合理的其他表达也可。",
         "explanation": "不能只根据块数判断，需要同时核对整体与平均分。" if subject else "核对是否说出可观察的事情及自己的感受，不要求背诵示例。", "timing": "in_class", "mode": "objective" if subject else "rubric", "criteria": [] if subject else ["描述具体事情", "表达自己的感受而不推断对方动机"]},
        {"id": "Q2", "activity_id": "A6", "goal_ids": ["G2", "G3"], "knowledge_ids": ["K3", "K4"],
         "prompt": "把一张长方形纸平均分成五份，取出一份。这一份怎样表示？分母5表示什么？" if subject else "请为彩笔情境写一段回应，包含复述确认和一个具体可商量的请求，并说明这样说的理由。",
         "answer": "表示为1/5；5表示这张纸被平均分成五份。" if subject else "表达可以多样；例如先确认对方还要用多久，再提出轮换或分开使用的具体请求。",
         "explanation": "分母表示整体平均分的份数，不是被取走小块的编号。" if subject else "依据倾听与请求是否具体评议，不按是否使用某一句固定台词判断。", "timing": "after_class", "mode": "objective" if subject else "rubric", "criteria": [] if subject else ["复述并确认对方的意思", "提出具体可行的请求", "理由回应双方需要"]},
        {"id": "Q3", "activity_id": "A6", "goal_ids": ["G2", "G3"], "knowledge_ids": ["K3", "K4"],
         "prompt": "隔几天再独立作答：把一个整体平均分成八份，指出其中一份，写出分数并解释分母。" if subject else "隔几天记录一次匿名的沟通尝试，描述自己的表达、对方回应和准备调整的地方。",
         "answer": "1/8；8表示整体平均分成八份，需要明确指的是哪一个整体。" if subject else "不设唯一答案，依据是否具体描述沟通动作和反思调整进行反馈。",
         "explanation": "使用新情境检查能否独立解释，记录与课堂的时间间隔。" if subject else "观察具体尝试的过程；未尝试或结果不理想都可如实记录。", "timing": "delayed", "mode": "objective" if subject else "rubric", "criteria": [] if subject else ["具体描述尝试", "描述回应而不贴标签", "提出下一步调整"]},
    ]
    slides[8]["question_ids"] = ["Q1"]
    slides[11]["question_ids"] = ["Q2", "Q3"]
    design = {"title": title, "course_type": kind, "grade": grade, "subject": "数学" if subject else "", "duration_minutes": 40,
              "goals": goals, "knowledge_points": knowledge, "activities": activities, "slides": slides, "questions": questions,
              "board_design": "一个整体 → 平均分成几份 → 其中的一份 → 几分之一" if subject else "事实与感受 → 倾听复述 → 具体请求 → 协商办法",
              "source_ids": [], "context_notes": "工程示例，未与具体教材版本对齐；相关学情、教师选择及检测数据均为模拟，不代表真实课堂效果。"}
    design = LessonDesign.model_validate(design).model_dump(mode="json")
    request = {"title": title, "course_type": kind, "grade": grade, "subject": design["subject"], "duration_minutes": 40, "class_label": "演示班级（模拟）", "class_size": 30,
               "prior_knowledge": "模拟条件：已接触平均分" if subject else "模拟条件：能描述基本感受", "difficulties": None, "constraints": ["保留核心目标与40分钟总课时"], "source_ids": []}
    feedback = {"raw_text": "模拟反馈：学生讨论较热烈，但课后对应任务的独立完成情况仍有困难。以下人数仅用于程序验证。", "observed_at": "2026-11-05T16:00:00+08:00",
                "participation": [{"activity_id": "A4", "participating": 24, "eligible": 30, "basis": "observed", "notes": "模拟去重人数"}],
                "timings": [{"activity_id": "A3", "actual_minutes": 10 if subject else 9, "basis": "observed"}],
                "assessments": [{"question_id": "Q2", "correct" if subject else "met_criteria": 12, "total": 30, "independent": True, "timing": "after_class", "days_after": 0, "basis": "observed", "errors": ["模拟错因：分母含义不清" if subject else "模拟问题：复述不准确，请求不够具体"]}],
                "observations": [], "teacher_keep": ["保留核心目标和总课时"]}
    decision = {"teacher_confirmed": True, "purpose": "same_lesson", "choices": [{"suggestion_id": "assessment_1", "action": "accept", "instructions": "模拟教师选择：补充有针对性的示例与独立检测"}], "keep_requirements": ["保留核心目标和40分钟课时"], "protected_ids": ["goal:G1", "goal:G2", "goal:G3"], "allow_goal_changes": False, "allow_duration_change": False}
    revised = copy.deepcopy(design)
    if subject:
        revised["activities"][2]["duration_minutes"] += 2
        revised["activities"][3]["duration_minutes"] -= 2
        revised["slides"][5]["points"] = ["对比平均分的四份与大小不等的四块。", "只有平均分，才能用几分之一表示其中一份。", "独立说明分母表示什么，再与同桌核对。"]
        revised["slides"][5]["teacher_script"] += "针对反馈中的困难，先比较两幅都是四块的图，要求学生独立说明为什么只有平均分的图能表示四分之一。再让学生指着整体解释分母，不只重复数字。"
    else:
        revised["activities"][3]["duration_minutes"] += 2
        revised["activities"][4]["duration_minutes"] -= 2
        revised["slides"][7]["points"] = ["先独立写一句复述，再请同伴确认。", "将模糊要求改为具体可执行的请求。", "核对办法怎样回应双方需要。"]
        revised["slides"][7]["teacher_script"] += "针对模拟反馈，先增加一次独立书面回应，避免只有个别同学替全组表达。教师展示一个复述遗漏对方需要的反例，再请学生给出具体修改。"
    revised["questions"][1]["prompt"] = "用新图独立作答：把一个整体平均分成六份，取其中一份，写出分数并解释每个数字的含义。" if subject else "在展示顺序争议的新情境中，先准确复述一方需要，再提出具体请求并说明理由。"
    revised["questions"][1]["answer"] = "1/6；6表示平均分成六份，1表示其中一份。" if subject else "允许多种方案；复述要准确，请求要具体，并说明怎样回应双方需要。"
    after = LessonDesign.model_validate(revised)
    changes = design_diff(LessonDesign.model_validate(design), after)
    revision = {"design": after.model_dump(mode="json"), "summary": "依据模拟反馈调整示例、独立检测和环节时间；这是工程演示，不代表真实教师决策。",
                "change_links": [{"target": change["target"], "suggestion_ids": ["assessment_1"], "explanation": "围绕模拟检测暴露的问题调整，保持核心目标和总课时。"} for change in changes]}
    return {"request": request, "design": design, "feedback": feedback, "decision": decision, "revision": revision}


def main():
    for kind in ["subject", "theme"]:
        directory = ROOT / "examples" / kind
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in lesson(kind).items():
            (directory / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Synthetic fixtures written: subject + theme")


if __name__ == "__main__":
    main()
