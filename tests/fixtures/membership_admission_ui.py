"""Local-only UI fixture; never connects to a database or external API."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import RLock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.membership_admission_content import get_admission_config
from backend.app.membership_policy import grade_membership_quiz


PROFILE = {
    "legal_name": "隔離申請人", "phone": "0912345678", "birth_date": "2000-01-01",
    "address": "隔離市合作路1號", "emergency_contact": "隔離聯絡人 0987654321",
    "contact_email": "applicant@example.test", "gender": None, "place_of_origin": None,
    "occupation": None, "registered_address": None, "correspondence_address": None,
    "landline_phone": None, "line_id": None,
}
SCENARIOS = {
    "new": "首次申請（從空白開始）", "70": "70分、不允許重考", "pass": "80分、待送審",
    "supplement": "管理員要求補件", "409": "首次儲存版本衝突", "admin": "管理員審核學生申請",
    "approved": "審核同意、尚無繳費或資格", "member": "既有正式社員資格頁",
    "history": "第二輪新申請、第一輪70分已撤回",
}
LOCK = RLock()
STATE: dict = {}
UI_ORIGIN = "http://127.0.0.1:4279"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def round_record():
    config = get_admission_config()
    return {
        "id": "00000000-0000-4000-8000-000000000101", "user_id": "isolated-applicant", "round_number": 1,
        "version": 1, "flow_version": "education-review-v1", "status": "draft", "profile": None,
        "requested_share_count": 10, "student_claimed": False, "discovery_channel": None, "discovery_other": None,
        "declaration_version": None, "privacy_version": None, "consented_at": None,
        "material_version": None, "material_completed_at": None, "quiz_version": config["quiz"]["version"],
        "quiz_eligibility": {"allowed": True, "remaining_attempts": 1, "next_available_at": None, "reason": None, "settings_configured": False},
        "attempts": [], "submissions": [], "reviews": [], "submitted_at": None, "reviewed_at": None,
        "review_reason": None, "student_verified": None, "decision_reference": None,
        "created_at": timestamp(), "updated_at": timestamp(), "can_edit": True, "can_submit": False, "can_withdraw": True,
    }


def prepared_round(score=80):
    config = get_admission_config()
    value = round_record()
    value.update({"version": 4, "profile": deepcopy(PROFILE), "discovery_channel": "朋友介紹",
                  "declaration_version": config["declaration"]["version"], "privacy_version": config["declaration"]["privacy_version"],
                  "consented_at": timestamp(), "material_version": config["material"]["version"], "material_completed_at": timestamp()})
    # Results are fixture responses only. The actual answer key remains server-side.
    answers = [{"question_id": question["id"], "option": question["options"][0]["value"]} for question in config["quiz"]["questions"]]
    from backend.app.membership_policy import _QUIZ_QUESTIONS
    correct_options = {question.id: question.correct_option for question in _QUIZ_QUESTIONS}
    results = []
    for index, answer in enumerate(answers):
        correct = index < score // 10
        correct_option = correct_options[answer["question_id"]]
        wrong_option = next(option["value"] for option in config["quiz"]["questions"][index]["options"] if option["value"] != correct_option)
        results.append({"question_id": answer["question_id"], "selected_option": correct_option if correct else wrong_option, "correct_option": correct_option, "correct": correct})
    value["attempts"] = [{"id": str(uuid4()), "attempt_number": 1, "quiz_version": config["quiz"]["version"], "score": score, "passed": score >= 80,
                          "submitted_at": timestamp(), "settings_version": 0, "results": results}]
    value["can_submit"] = score >= 80
    value["quiz_eligibility"] = {"allowed": False, "remaining_attempts": 0, "next_available_at": None,
                                "reason": "本輪已合格" if score >= 80 else "重考次數尚未設定，目前只允許首次交卷", "settings_configured": False}
    return value


def reset(scenario):
    if scenario not in SCENARIOS:
        raise ValueError("未知隔離場景")
    STATE.clear()
    STATE.update({"scenario": scenario, "round": None, "past_rounds": [], "writes": [], "unknown": [], "role": "admin" if scenario == "admin" else "customer", "conflict_pending": scenario == "409", "deny_read": False, "deny_write": False})
    if scenario in {"70", "pass", "supplement", "admin", "approved"}:
        STATE["round"] = prepared_round(70 if scenario == "70" else 80)
    if scenario in {"supplement", "admin", "approved"}:
        value = STATE["round"]
        value["submitted_at"] = timestamp()
        value["submissions"] = [{"id": str(uuid4()), "revision_number": 1, "submitted_at": value["submitted_at"]}]
        value["status"] = {"supplement": "needs_supplement", "admin": "submitted", "approved": "approved"}[scenario]
        value["can_edit"] = scenario == "supplement"
        value["can_submit"] = scenario == "supplement"
        if scenario == "admin":
            value.update({"student_claimed": True, "requested_share_count": 5})
        else:
            value.update({"reviewed_at": timestamp(), "review_reason": "請確認聯絡電話後重新送審" if scenario == "supplement" else "隔離審核同意", "decision_reference": None if scenario == "supplement" else "ISOLATED-DECISION"})
    if scenario == "history":
        previous = prepared_round(70)
        previous.update({"status": "withdrawn", "can_edit": False, "can_submit": False, "can_withdraw": False})
        STATE["past_rounds"] = [previous]
        STATE["round"] = {**round_record(), "id": str(uuid4()), "round_number": 2}


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def respond(self, value, status=200, content_type="application/json; charset=utf-8"):
        raw = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else value.encode()
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin == UI_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        if self.headers.get("Origin") != UI_ORIGIN:
            return self.respond({"detail": "Only the isolated UI origin is allowed"}, 403)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", UI_ORIGIN)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def controls(self, message=""):
        links = "".join(f'<li><a href="/__fixture?scenario={escape(key)}">{escape(label)}</a></li>' for key, label in SCENARIOS.items())
        target = "/admin/social" if STATE["role"] == "admin" else "/membership"
        self.respond(f'<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>入社UI隔離驗收</title><body><h1>入社UI隔離驗收</h1><p>目前場景：{escape(SCENARIOS[STATE["scenario"]])}</p><p>{escape(message)}</p><p><a href="{escape(UI_ORIGIN + target)}">開啟隔離網站（切換場景後重新載入）</a></p><ul>{links}</ul><h2>目前輪次操作</h2><ul><li><a href="/__fixture/approve">模擬管理員審核同意</a></li><li><a href="/__fixture/allow-retry">模擬管理員允許再考一次</a></li><li><a href="/__fixture/bump-version">模擬另一頁更改版本</a></li><li><a href="/__fixture/deny-read">模擬讀取401（回網站更新進度）</a></li><li><a href="/__fixture/deny-write">模擬寫入403（回網站儲存）</a></li><li><a href="/__fixture/state">檢查隔離寫入紀錄</a></li></ul></body></html>', content_type="text/html; charset=utf-8")

    def dispatch(self):
        with LOCK:
            return self.handle_locked()

    def handle_locked(self):
        url = urlparse(self.path)
        path = url.path
        if path in {"/", "/__fixture"}:
            scenario = parse_qs(url.query).get("scenario", [None])[0]
            if scenario:
                if scenario not in SCENARIOS:
                    return self.respond({"detail": "未知隔離場景"}, 400)
                reset(scenario)
            return self.controls()
        if path == "/__fixture/state":
            return self.respond(STATE)
        if path.startswith("/__fixture/"):
            value = STATE["round"]
            if not value:
                return self.controls("請先建立一輪申請。")
            if path.endswith("/approve"):
                value.update({"status": "approved", "reviewed_at": timestamp(), "review_reason": "隔離審核同意", "decision_reference": "ISOLATED-DECISION", "can_edit": False, "can_submit": False, "can_withdraw": False})
            elif path.endswith("/allow-retry"):
                value["quiz_eligibility"] = {"allowed": True, "remaining_attempts": 1, "next_available_at": None, "reason": None, "settings_configured": True}
            elif path.endswith("/bump-version"):
                pass
            elif path.endswith("/deny-read"):
                STATE["deny_read"] = True
            elif path.endswith("/deny-write"):
                STATE["deny_write"] = True
            else:
                return self.respond({"detail": "Unknown fixture control"}, 404)
            value["version"] += 1
            return self.controls("已更新隔離資料，請回網站按更新進度。")
        if self.headers.get("Origin") not in {None, UI_ORIGIN}:
            return self.respond({"detail": "Only the isolated UI origin is allowed"}, 403)
        if path == "/v1/auth/refresh":
            return self.respond({"access_token": "isolated-ui-token", "user": {"id": "isolated-admin" if STATE["role"] == "admin" else "isolated-applicant", "email": PROFILE["contact_email"], "display_name": "隔離管理員" if STATE["role"] == "admin" else "隔離申請人", "user_role": STATE["role"], "membership_type": "member" if STATE["scenario"] == "member" else "nonmember"}})
        if path == "/v1/members/me":
            return self.respond({"membership_type": "member" if STATE["scenario"] == "member" else "nonmember", "membership": {"status": "active", "member_number": "ISOLATED001"} if STATE["scenario"] == "member" else None})
        if path == "/v1/platform":
            return self.respond({"orders_open": True, "sales_scope": "pickup", "closed_message": ""})
        if path in {"/v1/admin/activities", "/v1/admin/member-proposals", "/v1/admin/membership-applications", "/v1/wishes", "/v1/admin/meetings", "/v1/admin/members", "/v1/admin/member-roster", "/v1/admin/activity-refund-requests"}:
            return self.respond([])
        if path == "/v1/membership/application":
            return self.respond({"detail": "沒有舊版申請"}, 404)
        if path == "/v1/membership/admission":
            if STATE["deny_read"]:
                return self.respond({"detail": "隔離登入已失效"}, 401)
            value = STATE["round"]
            history = [{key: row[key] for key in ("id", "round_number", "status", "created_at", "reviewed_at")} for row in ([value] if value else []) + STATE["past_rounds"]]
            return self.respond({"config": get_admission_config(), "current_round": value, "history": history, "profile_defaults": None,
                                 "can_start": not value or value["status"] in {"withdrawn", "rejected"}, "start_blocked_reason": None})
        if path == "/v1/admin/membership/admissions" and self.command == "GET":
            if STATE["role"] != "admin":
                return self.respond({"detail": "無管理員權限"}, 403)
            query = parse_qs(url.query)
            value = STATE["round"]
            status = query.get("status", ["pending"])[0]
            keyword = query.get("q", [""])[0].lower()
            matches = value and (status == "all" or status == "pending" and value["status"] in {"submitted", "needs_supplement"} or status == value["status"])
            matches = matches and (not keyword or keyword in json.dumps(value["profile"], ensure_ascii=False).lower())
            return self.respond({"items": [value] if matches else [], "total": int(bool(matches)), "limit": 25, "offset": 0})
        if path.startswith("/v1/membership/admission/rounds/") and self.command == "GET":
            value = next((row for row in ([STATE["round"]] if STATE["round"] else []) + STATE["past_rounds"] if row["id"] == path.rsplit("/", 1)[-1]), None)
            return self.respond(value) if value else self.respond({"detail": "找不到本輪申請"}, 404)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        STATE["writes"].append({"method": self.command, "path": path, "body": body})
        if STATE["deny_write"]:
            return self.respond({"detail": "隔離權限已失效"}, 403)
        if path == "/v1/membership/admission/rounds" and self.command == "POST":
            if not STATE["round"] or STATE["round"]["status"] in {"withdrawn", "rejected"}:
                previous = STATE["round"]
                if previous:
                    STATE["past_rounds"].append(deepcopy(previous))
                STATE["round"] = {**round_record(), "id": str(uuid4()), "round_number": previous["round_number"] + 1 if previous else 1}
            return self.respond(STATE["round"])
        value = STATE["round"]
        if value and (path.startswith("/v1/membership/admission/rounds/") or path.startswith("/v1/admin/membership/admissions/")):
            if body.get("expected_version") != value["version"]:
                return self.respond({"detail": "申請版本已變更，請重新讀取"}, 409)
            if self.command == "PUT":
                if STATE["conflict_pending"]:
                    STATE["conflict_pending"] = False
                    value["version"] += 1
                    return self.respond({"detail": "模擬另一頁更改版本，請重新讀取"}, 409)
                allowed = {"profile", "requested_share_count", "student_claimed", "discovery_channel", "discovery_other", "declaration_version", "privacy_version"}
                value.update({key: body[key] for key in allowed})
                value["consented_at"] = timestamp()
            elif path.endswith("/education"):
                value.update({"material_version": body["material_version"], "material_completed_at": timestamp()})
            elif path.endswith("/quiz-attempts"):
                config = get_admission_config()
                result = grade_membership_quiz(body["quiz_version"], body["answers"])
                from backend.app.membership_policy import _QUIZ_QUESTIONS
                correct = {question.id: question.correct_option for question in _QUIZ_QUESTIONS}
                attempt = {"id": str(uuid4()), "attempt_number": len(value["attempts"]) + 1, "quiz_version": body["quiz_version"], "score": result.score, "passed": result.passed, "submitted_at": timestamp(), "settings_version": 0,
                           "results": [{"question_id": answer["question_id"], "selected_option": answer["option"], "correct_option": correct[answer["question_id"]], "correct": answer["option"] == correct[answer["question_id"]]} for answer in body["answers"]]}
                value["attempts"].append(attempt)
                value["can_submit"] = result.passed
                value["quiz_eligibility"] = {"allowed": False, "remaining_attempts": 0, "next_available_at": None, "reason": "本輪已合格" if result.passed else "重考次數尚未設定，目前只允許首次交卷", "settings_configured": False}
            elif path.endswith("/submit"):
                value.update({"status": "submitted", "can_edit": False, "can_submit": False, "submitted_at": timestamp()})
                value["submissions"].append({"id": str(uuid4()), "revision_number": len(value["submissions"]) + 1, "submitted_at": timestamp()})
            elif path.endswith("/withdraw"):
                value.update({"status": "withdrawn", "can_edit": False, "can_submit": False, "can_withdraw": False})
            elif path.endswith("/review"):
                decision = body["decision"]
                if STATE["role"] != "admin":
                    return self.respond({"detail": "無管理員權限"}, 403)
                if decision == "approve" and (not body.get("decision_reference") or value["student_claimed"] and not body.get("student_verified")):
                    return self.respond({"detail": "請填決議並核驗學生資格"}, 422)
                value.update({"status": {"approve": "approved", "request_supplement": "needs_supplement", "reject": "rejected"}[decision], "reviewed_at": timestamp(), "review_reason": body["reason"], "decision_reference": body.get("decision_reference"), "student_verified": body.get("student_verified"), "can_edit": decision == "request_supplement", "can_submit": decision == "request_supplement", "can_withdraw": decision == "request_supplement"})
                value["reviews"].append({"id": str(uuid4()), "decision": decision, "reason": body["reason"], "decision_reference": body.get("decision_reference"), "student_verified": body.get("student_verified"), "reviewed_at": timestamp()})
            else:
                STATE["unknown"].append(f"{self.command} {path}")
                return self.respond({"detail": "Unknown isolated mutation"}, 404)
            value["version"] += 1
            value["updated_at"] = timestamp()
            return self.respond(value)
        STATE["unknown"].append(f"{self.command} {path}")
        return self.respond({"detail": "Unknown isolated endpoint"}, 404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4278)
    parser.add_argument("--ui-origin", default=UI_ORIGIN)
    args = parser.parse_args()
    parsed = urlparse(args.ui_origin)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path not in {"", "/"} or parsed.scheme != "http":
        parser.error("UI origin must be an isolated localhost HTTP origin")
    UI_ORIGIN = args.ui_origin.rstrip("/")
    reset("new")
    print(f"Admission UI fixture: http://127.0.0.1:{args.port}/__fixture", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler).serve_forever()
