"""Generate campus content through deployed authenticated APIs and real LLM calls.

No database writes. Persist conversation IDs and idempotency keys for resuming.
"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "local-data" / "campus-live-generation.json"
BASE = "http://localhost:3100/api/v1"
BATCH = "campus-live-20260923-v1"
LOCK = threading.Lock()
JOBS = [
    ("teacher", "高一语文｜荷塘月色教学设计", "为高一语文《荷塘月色》设计45分钟课堂。聚焦通感、情景交融和朗读，写明教学目标、每环节时间、3个递进问题及参考回答、板书和退出卡。引用原文须准确且简短。"),
    ("teacher", "高一数学｜函数单调性分层练习", "生成高一函数单调性分层练习：基础3题、提升2题、挑战1题。逐题附答案和关键步骤，注意定义域及端点，最后解释两个常见错误。自行复核计算。"),
    ("teacher", "高二英语｜读后续写课堂活动", "设计高二英语读后续写课。原创120词故事开头及两段续写首句，提供情节设计活动、情绪与动作表达、100词参考续写和评价量表。英文难度适合普通高二。"),
    ("teacher", "高一物理｜速度时间图像练习", "设计高一匀变速直线运动练习，包含可用文字准确作图的分段速度时间数据，4道有梯度的问题，完整计算和单位，解释位移与路程区别。"),
    ("teacher", "高二化学｜反应速率探究课", "设计高二化学反应速率的控制变量探究课，给出教师演示方案、变量表、观察表、讨论题和安全注意事项。避免危险药品组合，区分理论预期和实际观察，不编造实验结果。"),
    ("teacher", "高一生物｜细胞膜概念辨析", "生成高一细胞膜结构与功能教学资料，含概念图文字版、6道判断改错题及解析、生活情境题、选择透过性与流动性的区别。"),
    ("teacher", "高二历史｜工业革命史料研读", "设计工业革命影响的史料研读课。使用明确标为教学自编的材料，不伪造历史引文；从生产、城市、环境三个维度设置问题、参考答案和论证评价标准。"),
    ("teacher", "高一地理｜昼夜温差情境题", "为高一大气受热过程生成四道生活情境题及解析，涉及晴天阴天、沙漠、温室、霜冻，区分太阳短波和地面长波辐射，给出板书示意。"),
    ("teacher", "高二政治｜校园文化项目学习", "围绕文化传承与创新设计高二校园文化项目学习，包含驱动问题、一周安排、学生分工、成果形式和评价量表。不要虚构学校现有制度。"),
    ("teacher", "高三语文｜议论文论证训练", "制作高三议论文论证结构训练：原创一个薄弱论证段，指出问题，给出两种改写及分析，再提供自查表。主题为技术使用与独立思考。"),
    ("teacher", "高三数学｜数列求和专题", "编写高三数列求和微专题，分别给出裂项相消、错位相减、分组求和的例题、完整解析和适用条件，并提供一道迁移练习及答案，复核结果。"),
    ("teacher", "高一英语｜校园活动写作", "生成高一英语校园活动写作学习单，包含80词左右原创范文、词块、结构拆解、常见时态错误和分层任务，附简明评分标准。"),
    ("teacher", "班会｜数字素养与信息核验", "设计40分钟高中班会：面对网络信息如何核验。提供两个原创情境、分组讨论流程、教师引导语和可操作的信息核验清单，避免声称已发生真实事件。"),
    ("teacher", "班会｜考试前的学习安排", "设计高中考试前学习安排主题班会，包含40分钟流程、一周计划空表、错题复盘方法和同伴支持活动。使用支持性语言，不作心理诊断。"),
    ("teacher", "语文备课组｜单元教学规划", "为高一现代散文阅读单元拟定4课时教学规划，给出单元目标、每课时任务、形成性评价和分层作业，注明教材版本未指定，避免捏造教材篇目。"),
    ("teacher", "数学教研｜错因分析记录表", "设计教师可用的数学错因分析工具：分类标准、空白记录表、用自编题展示一次分析、二次教学决策规则。不得虚构班级真实成绩或学生信息。"),
    ("teacher", "家校沟通｜阅读习惯共育建议", "起草面向高中家长的阅读习惯共育建议，约600字，包含家庭可实行的三项行动、如何尊重阅读差异和反馈方式，不使用不存在的学校政策或联系人。"),
    ("teacher", "英语教研｜课堂观察量表", "设计英语课堂学生参与度观察量表，含可观察行为、抽样记录方式、等级标准、课后反馈提纲，区分参与频次与学习质量，不含虚构观察结论。"),
    ("admin", "教务工作｜集体备课实施建议", "起草高中集体备课实施建议稿，含目标、会前准备、60分钟议程、成果要求、责任分工角色和跟进机制。不声称已经审批，具体日期人员用待确定。"),
    ("admin", "校园活动｜读书月策划草案", "起草高中校园读书月活动策划，含四周安排、活动说明、资源需求、风险预案和效果评价。经费使用待核定，不虚构预算审批或实际参与人数。"),
    ("admin", "教师培训｜AI辅助备课工作坊", "设计90分钟AI辅助备课教师培训，含分分钟议程、实操任务、提示词示例、内容核验清单和结业作品要求。说明隐私保护与教师审校责任。"),
    ("admin", "教研会议｜议程与纪要模板", "生成高中跨学科教研会的可用议程和空白纪要模板，包含议题、讨论结论栏、行动项、负责人和截止时间栏。不编造已经发生的会议和结论。"),
    ("admin", "办公通知｜公开课征集草稿", "起草高中公开课征集通知及报名表，语气清晰克制，含申报内容、材料清单、评审维度和组织流程，日期、地点、联系人均标为待填写。"),
    ("admin", "教学管理｜教学资源质量核验清单", "制定高中AI辅助教学资源质量核验清单，按知识准确性、年龄适宜性、课堂可用性、版权引用、个人信息保护分类，给出判断标准和发现问题的处理步骤。"),
]
SKILLS = ["分层作业设计", "单元教学规划", "课堂提问设计", "教研纪要整理", "班会活动策划", "家校沟通草稿"]
for name in SKILLS:
    JOBS.append(("admin", "技能编写｜" + name,
        f"请编写一个用于{name}的高质量AI助手系统提示词。只输出提示词正文，包含角色、所需输入、信息缺失时的处理、工作步骤、输出格式和自查要求。严禁编造学校制度、实际会议记录或学生信息，输出须供教师审核。控制在900字以内。"))


def main():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"batch": BATCH, "jobs": {}}
    assert state["batch"] == BATCH
    def persist():
        temp = STATE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(STATE)
    def call(client, method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.is_error:
            try:
                code = response.json().get("code", "unknown")
            except ValueError:
                code = "non_json"
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}, {code}")
        return response.json()["data"]
    admin_password = os.environ["DEPLOY_ADMIN_PASSWORD"]
    cookies = {}
    for username, password in [("admin", admin_password), ("teacher", os.getenv("DEPLOY_TEACHER_PASSWORD", "123"))]:
        with httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:3100"}, timeout=240, trust_env=False) as client:
            call(client, "POST", "/auth/login", json={"username": username, "password": password})
            cookies[username] = dict(client.cookies)
            quota = call(client, "GET", "/me/quota")
            print(json.dumps({"account": username, "available": quota["available"], "consumed": quota["totalConsumed"]}), flush=True)

    def work(index, job):
        username, title, prompt = job
        key = str(index)
        with LOCK:
            item = state["jobs"].setdefault(key, {"title": title, "username": username})
            if item.get("done"):
                return {"index": index, "status": "already_done"}
        with httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:3100"}, cookies=cookies[username], timeout=240, trust_env=False) as client:
            if not item.get("conversationId"):
                convo = call(client, "POST", "/conversations", json={"title": title})
                with LOCK:
                    item["conversationId"] = convo["id"]
                    persist()
            cid = item["conversationId"]
            turns = [prompt + "\n请生成实质内容，控制在1000字以内；没有真实信息的地方明确留空。"]
            if index < 24 and index % 4 == 0:
                turns.append("请检查刚才内容的准确性与可实施性，修正可能的问题，并给出可以保存使用的完整修订版，控制在1000字以内。不要声称已由真人审核。")
            for turn, content in enumerate(turns):
                if item.get("turns", 0) > turn:
                    continue
                response = client.post(f"/conversations/{cid}/messages", json={"content": content, "useKnowledge": False},
                    headers={"Idempotency-Key": f"{BATCH}:{index}:{turn}"})
                if response.status_code != 200 or "event: message.completed" not in response.text:
                    raise RuntimeError(f"model generation incomplete: job={index}, turn={turn}, HTTP={response.status_code}")
                with LOCK:
                    item["turns"] = turn + 1
                    persist()
            messages = call(client, "GET", f"/conversations/{cid}/messages")["items"]
            answers = [m for m in messages if m["role"] == "assistant" and m["status"] == "completed"]
            assert len(answers) == len(turns) and all(len(m["content"]) > 120 for m in answers)
            # The messages endpoint returns chronological items without timestamps.
            answer = answers[-1]
            draft = call(client, "GET", f"/conversations/{cid}/draft")
            saved = call(client, "PUT", f"/conversations/{cid}/draft", json={"content": answer["content"], "revision": draft["revision"]})
            if index >= 24 and not item.get("skillId"):
                skill = call(client, "POST", "/skills", json={"name": SKILLS[index-24],
                    "description": "由当前接入的大模型生成的校园工作助手提示词，待教师审核。",
                    "prompt": answer["content"], "scope": "private"})
                item["skillId"] = skill["id"]
            with LOCK:
                item.update(done=True, answerCharacters=len(answer["content"]), draftRevision=saved["revision"])
                item.pop("error", None)
                persist()
            return {"index": index, "title": title, "turns": len(turns), "characters": len(answer["content"]), "status": "completed"}
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = {pool.submit(work, i, job): i for i, job in enumerate(JOBS)}
            for future in as_completed(pending):
                index = pending[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"index": index, "status": "failed", "error": str(exc)}
                    with LOCK:
                        state["jobs"][str(index)]["error"] = str(exc)
                        persist()
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        for username in cookies:
            with httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:3100"}, cookies=cookies[username], timeout=30, trust_env=False) as client:
                quota = call(client, "GET", "/me/quota")
                print(json.dumps({"account": username, "available": quota["available"], "consumed": quota["totalConsumed"], "reserved": quota["reserved"]}), flush=True)
                call(client, "POST", "/auth/logout")
    assert all(state["jobs"].get(str(i), {}).get("done") for i in range(len(JOBS))), "Some jobs require attention"


if __name__ == "__main__":
    main()
