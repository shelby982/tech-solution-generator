"""评分点关键词核对：只检查正文是否回应了评分点关键词。"""
from services.scoring_point_review import review_block_points


def test_keywords_and_coverage():
    block = {"content": "本项目背景说明客户管理现状。建设目标是统一台账。项目需求包括统计数据。"}
    points = [
        {"point_id": "p1", "factor": "项目理解", "item": "项目需求理解完整性", "points": 4,
         "criterion": "包含：1）项目背景；2）项目目标；3）项目建设范围；4）项目需求及特点。每提供一项得1分。"},
        {"point_id": "p2", "factor": "项目团队", "item": "项目负责人", "points": 10,
         "criterion": "项目负责人具备PMP证书，并提供社保缴纳证明。"},
    ]
    result = review_block_points(block, points)
    first, second = result["points"]
    assert first["status"] in ("部分回应", "已回应")
    assert any(k["keyword"] == "项目背景" and k["matched"] for k in first["keywords"])
    assert second["status"] == "未回应"
    assert result["total"] == 2
