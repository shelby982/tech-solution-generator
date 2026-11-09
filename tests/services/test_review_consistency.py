from services.review_consistency import check_consistency


def _row(code, score=90, satisfied="satisfied", issues=None):
    return {"block_code": code, "title": code, "score": score,
            "satisfied": satisfied, "issues": issues or []}


def test_consistency_flags_duplicate_issue_and_conflict():
    out = check_consistency([
        _row("s1", 60, "satisfied", [{"point": "未提供证书", "severity": "high"}]),
        _row("s2", 90, "satisfied", [{"point": "未提供证书", "severity": "medium"}]),
        _row("s3", 95, "unsatisfied"),
    ])
    types = {f["type"] for f in out["findings"]}
    assert "duplicate_issue" in types
    assert "score_satisfied_conflict" in types
    assert out["status"] == "attention"


def test_consistency_ok_for_clean_chapters():
    out = check_consistency([_row("s1", 90), _row("s2", 85)])
    assert out == {"status": "ok", "findings": [], "chapters_checked": 2}
