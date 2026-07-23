from app import platforms


def test_site_search_never_relabels_off_domain_result(monkeypatch):
    monkeypatch.setattr(
        platforms,
        "search_engine",
        lambda *_args, **_kwargs: [{
            "title": "杭州市",
            "url": "https://baike.baidu.com/item/hangzhou",
            "snippet": "百科摘要",
        }],
    )

    assert platforms.site_search(
        ["xiaohongshu.com", "xhslink.com"],
        "杭州西湖餐厅",
        enrich=False,
    ) == []


def test_site_search_accepts_exact_or_subdomain_only(monkeypatch):
    monkeypatch.setattr(
        platforms,
        "search_engine",
        lambda *_args, **_kwargs: [
            {
                "title": "点评结果",
                "url": "https://www.dianping.com/discovery/1",
            },
            {
                "title": "伪造相似域名",
                "url": "https://dianping.com.example.test/fake",
            },
        ],
    )

    result = platforms.site_search(
        ["dianping.com"],
        "杭州西湖餐厅",
        enrich=False,
    )
    assert [row["title"] for row in result] == ["点评结果"]
