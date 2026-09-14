"""测试公司名称向量化器"""
import pytest
from bank_statement_matcher.src.utils.name_vectorizer import NameVectorizer


class TestNameVectorizer:

    def test_clean_stopwords(self):
        nv = NameVectorizer()
        assert nv._clean("深圳腾讯科技有限公司") == "深圳腾讯"
        assert nv._clean("华为技术有限公司") == "华为"
        assert nv._clean("招商银行股份有限公司") == "招商"
        assert nv._clean("") == ""

    def test_bigrams(self):
        nv = NameVectorizer()
        assert nv._bigrams("abcd") == ["ab", "bc", "cd"]
        assert nv._bigrams("ab") == []   # 长度≤2不分词
        assert nv._bigrams("a") == []
        assert nv._bigrams("") == []

    def test_fit_and_similarity(self):
        nv = NameVectorizer()
        names = [
            "深圳企鹅网络技术",
            "企鹅网络深圳",
            "华为技术",
        ]
        nv.fit(names)

        # 相同名称 = 1.0
        assert nv.cosine_similarity("深圳企鹅网络技术", "深圳企鹅网络技术") == pytest.approx(1.0)

        # 不同名称应该有合理的相似度
        sim = nv.cosine_similarity("深圳企鹅网络技术", "企鹅网络深圳")
        assert 0 < sim < 1

        # 完全不相关 = 0
        sim_hw = nv.cosine_similarity("深圳企鹅网络技术", "华为技术")
        assert sim_hw < sim  # 华为和企鹅的相似度应该更低

    def test_quick_pass_exact_match(self):
        nv = NameVectorizer()
        nv.fit(["测试公司A", "测试公司B"])
        # 清洗后完全相等
        assert nv.cosine_similarity("腾讯科技有限公司", "腾讯科技股份") == pytest.approx(1.0)

    def test_quick_pass_short_name(self):
        nv = NameVectorizer()
        nv.fit(["华为", "腾讯"])
        # 长度≤2 且互相包含
        assert nv.cosine_similarity("华为", "华为技术") == pytest.approx(1.0)
        assert nv.cosine_similarity("腾讯", "阿里巴巴") == pytest.approx(0.0)

    def test_empty_names(self):
        nv = NameVectorizer()
        assert nv.cosine_similarity("", "华为") == 0.0
        assert nv.cosine_similarity("华为", "") == 0.0

    def test_batch_similarity(self):
        nv = NameVectorizer()
        nv.fit(["深圳企鹅", "北京企鹅", "华为技术"])
        sims = nv.batch_similarity("深圳企鹅", ["北京企鹅", "华为技术"])
        assert len(sims) == 2
        assert sims[0] > sims[1]  # 深圳企鹅 和 北京企鹅 更相似
