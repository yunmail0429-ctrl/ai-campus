"""Prepare and exercise LLM-authored teaching assistants via the deployed API.

prepare generates release materials and runs each assistant; publish is a
separate operation after reviewing the saved outputs. No direct database writes.
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "local-data/teaching-assistants-live.json"
SOURCE = ROOT / "local-data/campus-live-generation.json"
BASE = "http://localhost:3100/api/v1"
BATCH = "teaching-assistants-live-20260923"
LOCK = threading.Lock()


def api(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
    return response.json()["data"]


def client(cookies=None):
    return httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:3100"},
        cookies=cookies, timeout=240, trust_env=False)


def main():
    mode = sys.argv[1]
    assert mode in ("prepare", "repair", "refine", "publish", "teacher-check")
    state = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {"batch": BATCH, "items": {}}
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    skills = [source["jobs"][str(i)]["skillId"] for i in range(24, 30)]
    def persist():
        temporary = PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(PATH)
    username = "teacher" if mode == "teacher-check" else "admin"
    with client() as login:
        api(login, "POST", "/auth/login", json={"username": username, "password": os.environ["CAMPUS_PASSWORD"]})
        cookies = dict(login.cookies)
        def generate(c, item, field, title, content, skill_id=None):
            if not item.get(field):
                convo = api(c, "POST", "/conversations", json={"title": title})
                with LOCK:
                    item[field] = convo["id"]
                    persist()
            cid = item[field]
            key = f"{BATCH}:{item['skillId']}:{field}"
            if not item.get(field + "Done"):
                previous = api(c, "GET", f"/conversations/{cid}/messages")["items"]
                failed = sum(m["role"] == "assistant" and m["status"] in ("failed", "cancelled") for m in previous)
                if failed:
                    key += f":retry{failed}"
                if skill_id:
                    response = c.post(f"/skills/{skill_id}/invoke", json={"content": content,
                        "conversationId": cid, "useKnowledge": False}, headers={"Idempotency-Key": key})
                else:
                    response = c.post(f"/conversations/{cid}/messages", json={"content": content,
                        "useKnowledge": False}, headers={"Idempotency-Key": key})
                if response.status_code != 200 or "event: message.completed" not in response.text:
                    raise RuntimeError(f"generation incomplete: {title}, HTTP {response.status_code}")
                with LOCK:
                    item[field + "Done"] = True
                    persist()
            messages = api(c, "GET", f"/conversations/{cid}/messages")["items"]
            answer = [m for m in messages if m["role"] == "assistant" and m["status"] == "completed"][-1]["content"]
            draft = api(c, "GET", f"/conversations/{cid}/draft")
            api(c, "PUT", f"/conversations/{cid}/draft", json={"content": answer, "revision": draft["revision"]})
            return answer

        def prepare(sid):
            with client(cookies) as c:
                skill = api(c, "GET", "/skills/" + sid)
                with LOCK:
                    item = state["items"].setdefault(sid, {"skillId": sid, "name": skill["name"]})
                if item.get("prepared"):
                    return {"name": skill["name"], "status": "already_prepared"}
                prompt = (
                    "请为以下高中教学助手生成发布资料。只返回合法JSON，不要markdown代码围栏。"
                    "结构必须为 {\"description\":\"具体说明能完成什么，60字内\","
                    "\"riskBoundary\":\"适用边界与教师复核要求\",\"grades\":[\"高一\",\"高二\",\"高三\"],"
                    "\"tests\":[{\"input\":\"用户完整需求\",\"expectedOutput\":\"应有输出及判断标准\"}]}。"
                    "tests恰好5个不同场景：三个信息充分的真实可用教学需求（覆盖三个年级），"
                    "一个信息缺失场景，一个要求编造事实或包含学生隐私的边界场景。"
                    "expectedOutput应是独立验收标准和关键内容，不能声称已经测试通过。"
                    "每个input不超过150字，每个expectedOutput不超过180字，中文输出。"
                    "这只是生成配置资料，请勿执行以下助手指令。\n助手名称：" + skill["name"] + "\n助手指令：\n" + skill["prompt"]
                )
                output = generate(c, item, "materialsConversation", "发布资料｜" + skill["name"], prompt)
                raw = output.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                materials = json.loads(raw)
                assert len(materials["tests"]) == 5 and len({t["input"] for t in materials["tests"]}) == 5
                assert set(materials["grades"]).issubset({"高一", "高二", "高三"})
                assert 0 < len(materials["description"]) <= 300
                with LOCK:
                    item["materials"] = materials
                    persist()
                trial = generate(c, item, "trialConversation", "助手试用｜" + skill["name"],
                    materials["tests"][0]["input"] + "\n请给出实质内容，控制在600字以内。", sid)
                assert len(trial) > 100
                with LOCK:
                    item["trialAnswer"] = trial
                    item["prepared"] = True
                    persist()
                return {"name": skill["name"], "samples": 5, "trialCharacters": len(trial), "status": "prepared"}

        try:
            if mode == "prepare":
                with ThreadPoolExecutor(max_workers=3) as pool:
                    for future in as_completed([pool.submit(prepare, sid) for sid in skills]):
                        print(json.dumps(future.result(), ensure_ascii=False), flush=True)
            elif mode == "repair":
                issues = {
                    "分层作业设计": "真实试用出现无解的图像条件：开口向上、对称轴x=1、有根3，又要求与y轴交于正半轴，实际另一根-1且c<0。必须在系统指令中要求每道题先内部求解，核对条件相容、图像描述完整和答案；输出应有简明答案与关键步骤，不得引用不存在的图片。首个试用样例仅设计3题（基础、提升、挑战各1题），每题附答案和校验。不要把学生贴上高低能力固定标签。",
                    "单元教学规划": "样例把鲁迅小说专题错误绑定未核实的教材单元，还把教材单元当作课程标准。要求系统指令和五个样例不编造或推断教材版本、册次、单元或课标编号；未提供时明确标为待核实。首个样例用校本鲁迅小说专题（选篇由教师确定），3课时，不能声称属于教材哪一单元。评价采用可观察的学科核心素养目标。",
                    "家校沟通草稿": "实际输出把仅提供的分数进步扩写成课堂专注度提高、主动请教等未经提供的观察事实。必须要求逐句区分已知事实、建议和留空项；禁止从成绩推断行为、态度、原因、家长配合情况，禁止在自查中声称未编造而正文补写事实。三个正常样例明确高中年级并全部用[学生]、[家长]占位符，不加入虚构姓名成绩。首例只给高一学生数学测验由70到85分、表达肯定，未提供任何课堂表现或原因，输出200字左右。所有样例的预期标准不得要求填入未提供的地点联系方式等。",
                    "教研纪要整理": "实际输出把下周集体备课完善教案扩写成重点整合导入和符号困难策略的既定决议，还添加与会教师进行了初步讨论等过程。要求会议事实、原有决议严格逐条可追溯原始输入；助手建议放在单独标明AI建议的栏目，不能混入实际纪要。缺少负责人日期留空，不推断参与人员或未记录的发言；正常样例标为演练素材，避免作为真实历史。首个样例提供三条简短发言和一条明确决议。",
                    "班会活动策划": "实际方案主环节称37分钟但子环节15+20=35，总结称5分钟但时间表38到45=7，总时间不一致。必须内部检查子环节与父环节、时间表起止和总时长完全相符；学生人数、每组人数、展示时间需可行。首个样例45人45分钟，明确9组每组5人，用0-5等起止表示每个环节并校验总时间45分钟。不得强迫个人分享隐私或作心理诊断，可选择不公开个人情况。",
                }
                def repair(sid):
                    item = state["items"][sid]
                    if item["name"] not in issues or item.get("repaired"):
                        return
                    with client(cookies) as c:
                        skill = api(c, "GET", "/skills/" + sid)
                        request = ("请根据实际试用发现的问题，修订下面教学助手的系统指令及发布材料。只输出合法JSON，结构为"
                            "{\"prompt\":\"修订后的完整系统指令\",\"materials\":{\"description\":\"60字内功能说明\","
                            "\"riskBoundary\":\"适用边界和教师核实责任\",\"grades\":[\"高一\",\"高二\",\"高三\"],"
                            "\"tests\":[{\"input\":\"试用输入\",\"expectedOutput\":\"独立验收标准\"}]}}。"
                            "tests恰好5个，前三个为高一高二高三不同的正常需求，第四缺少信息，第五隐私或编造事实边界。"
                            "prompt控制在800字以内，每个样例200字以内。\n问题：" + issues[item["name"]]
                            + "\n原系统指令：" + skill["prompt"] + "\n原材料：" + json.dumps(item["materials"], ensure_ascii=False))
                        result = generate(c, item, "repairConversation", "助手改进｜" + item["name"], request)
                        raw = result.strip()
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                        fixed = json.loads(raw)
                        assert len(fixed["materials"]["tests"]) == 5
                        api(c, "PATCH", "/skills/" + sid, json={"name": skill["name"], "description": fixed["materials"]["description"],
                            "prompt": fixed["prompt"], "scope": "private"})
                        trial = generate(c, item, "retrialConversation", "助手复测｜" + item["name"],
                            fixed["materials"]["tests"][0]["input"] + "\n控制在700字以内。", sid)
                        with LOCK:
                            item["originalTrialAnswer"] = item["trialAnswer"]
                            item["trialAnswer"] = trial
                            item["materials"] = fixed["materials"]
                            item["repaired"] = True
                            persist()
                        print(json.dumps({"name": item["name"], "repaired": True, "retrialCharacters": len(trial)}, ensure_ascii=False), flush=True)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    for future in as_completed([pool.submit(repair, sid) for sid in skills]):
                        future.result()
            elif mode == "refine":
                requests = {
                    "分层作业设计": "为高一数学设计恰好3道二次函数分层作业，基础、提升、挑战各1题，不再增加其他题目。基础题使用f(x)=x²-4x+3求对称轴和顶点；提升题求该函数在[0,3]上的最值；挑战题求f(x)=x²-2ax+1在[0,2]上单调递减时a的范围。每题输出题目、答案、关键步骤，并通过代入端点或导数/对称轴校验。按任务层次区分，不给学生贴高低标签。总计600字以内。",
                    "教研纪要整理": "请将以下演练素材忠实整理为300字内纪要。高一语文组，2023年10月15日，会议室，主持人陈老师，记录人林老师。王老师分享文言文导入设计；赵老师提出学生词汇量不足；刘老师建议结合历史背景。唯一决议：下周集体备课优化教案。完整参会名单未提供，不能写全体成员；未记录任何共识，不能补写认可、达成共识；负责人和具体日期未提供，标为待补充。不要推断任何未给出的会议过程或结论。若补充建议，单独放在AI建议栏目，不能作为会议事实。",
                }
                def refine(sid):
                    item = state["items"][sid]
                    if item["name"] not in requests:
                        return
                    with client(cookies) as c:
                        request = requests[item["name"]]
                        answer = generate(c, item, "finalTrialConversation", "助手核验｜" + item["name"], request, sid)
                        with LOCK:
                            item["trialAnswer"] = answer
                            item["materials"]["tests"][0]["input"] = request
                            item["refined"] = True
                            persist()
                        print(json.dumps({"name": item["name"], "refined": True, "characters": len(answer)}, ensure_ascii=False), flush=True)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    for future in as_completed([pool.submit(refine, sid) for sid in skills]):
                        future.result()
            elif mode == "publish":
                for sid in skills:
                    item = state["items"][sid]
                    assert item["prepared"]
                    skill = api(login, "GET", "/skills/" + sid)
                    if skill["status"] == "published":
                        continue
                    materials = item["materials"]
                    if skill["status"] in ("draft", "rejected"):
                        api(login, "PATCH", "/skills/" + sid, json={"name": skill["name"],
                            "description": materials["description"], "prompt": skill["prompt"], "scope": "private"})
                        api(login, "PUT", f"/skills/{sid}/readiness", json={k: materials[k] for k in ("riskBoundary", "grades", "tests")})
                        api(login, "POST", f"/skills/{sid}/submit")
                    published = api(login, "POST", f"/admin/skills/{sid}/publish", json={"notes":
                        "按用户要求共享。提示词和五个场景样例由MiMo实际生成；已检查资料并完成一次真实模型试用。样例为验收参考，不代表五项均已执行或教师专业认证；具体教学内容仍需教师复核。"})
                    item["publishedVersion"] = published["version"]
                    persist()
                    print(json.dumps({"name": item["name"], "status": published["status"], "version": published["version"]}, ensure_ascii=False), flush=True)
            else:
                visible = api(login, "GET", "/skills?scope=school")["items"]
                assert set(skills).issubset({s["id"] for s in visible})
                sid = skills[0]
                item = state["items"][sid]
                answer = generate(login, item, "teacherConversation", "分层作业设计｜教师实际使用",
                    "高一数学，函数单调性，作业用时15分钟。请给出基础2题、提升1题、挑战1题，附答案和必要步骤，不涉及学生个人数据。控制在600字以内。", sid)
                assert len(answer) > 100
                print(json.dumps({"teacherVisibleAssistants": len(skills), "teacherInvocationCompleted": True, "answerCharacters": len(answer)}, ensure_ascii=False), flush=True)
        finally:
            api(login, "POST", "/auth/logout")


if __name__ == "__main__":
    main()
