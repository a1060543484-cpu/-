"""业务规则的 golden test —— 把「已经验证过是对的行为」锁死。

这些数字不是拍脑袋写的，全部来自真实跑过的案例（见 AGENTS.md「业务不变量」）。
任何一个变了，要么是修 bug（那就同步改这里的期望并在 commit 里说明为什么），
要么是回归（那就必须修回来）。

跑法：
    python3 -m unittest discover -s tests -v        # 全部
    python3 -m unittest tests.test_rules -v         # 只跑这个文件
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: F401  ← 必须最先导入，它设好临时库路径

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "FastAPI"))

from app import db, seed                # noqa: E402
from app.agents import tools            # noqa: E402


def setUpModule() -> None:
    db.init()
    seed.seed(force=True)


def tearDownModule() -> None:
    _bootstrap.cleanup()


CTX: dict = {}


class CrimeMatchTest(unittest.TestCase):
    """罪名匹配：宁可不判，也不能把好人判成罪犯。"""

    def test_good_person_has_no_crime(self):
        """施粥济贫的善人不能有任何罪名 —— 这是修过的真 bug。"""
        r = tools.law_match_crime(CTX, "施粥济贫，修桥铺路，教化乡里，孝养父母")
        self.assertEqual(r["crime"], "", f"善人被误判：{r}")
        self.assertEqual(r["desc"], "无显著罪名")

    def test_offline_ocr_stub_is_ignored(self):
        """兜底 OCR 的「模拟文本」整段剔除，里面的「账册」不是真实案情。"""
        r = tools.law_match_crime(CTX, "【离线 OCR 模拟】识别到收据、借据、账册等文本。")
        self.assertEqual(r["crime"], "", f"兜底模板词被当成了罪证：{r}")

    def test_weak_alias_below_threshold(self):
        """只出现「账册」(1.0 分) 不足以定罪，阈值是 2.0。"""
        r = tools.law_match_crime(CTX, "账册")
        self.assertEqual(r["crime"], "")
        self.assertLess(r["score"], 2.0)

    def test_corruption(self):
        r = tools.law_match_crime(CTX, "侵吞赈灾粮款两千石")
        self.assertEqual(r["crime"], "贪污")

    def test_theft_by_logging(self):
        """「盗伐」是盗窃的别名，不能因为没写「盗窃」二字就漏判。"""
        r = tools.law_match_crime(CTX, "盗伐林木三十株")
        self.assertEqual(r["crime"], "盗窃")

    def test_explicit_crime_word_wins(self):
        r = tools.law_match_crime(CTX, "故意杀人")
        self.assertEqual(r["crime"], "杀人")

    def test_returns_candidates(self):
        r = tools.law_match_crime(CTX, "侵吞赈灾粮款")
        self.assertIsInstance(r["candidates"], list)
        self.assertLessEqual(len(r["candidates"]), 3)


class PenaltyMatchTest(unittest.TestCase):
    """量刑：基准天数 × clamp(sin/60, 0.05, 1.6)。"""

    def test_base_table(self):
        """sin=60 是基准线，ratio=1.0，天数即基准。"""
        cases = {
            "盗窃": (365, "刀山狱"),
            "杀人": (3285, "血池狱"),
            "欺诈": (540, "拔舌狱"),
            "贪污": (1825, "油锅狱"),
            "忤逆": (1825, "拔舌狱"),
            "邪淫": (720, "铜柱狱"),
            "诽谤": (365, "拔舌狱"),
            "逃税": (730, "磨盘狱"),
            "伤人": (180, "石压狱"),
            "纵火": (1095, "油锅狱"),
        }
        for crime, (days, prison) in cases.items():
            with self.subTest(crime=crime):
                r = tools.law_match_penalty(CTX, crime, 60)
                self.assertEqual(r["sentence_days"], days)
                self.assertEqual(r["prison"], prison)

    def test_cap_at_1_6(self):
        """sin=96 时 ratio 封顶 1.6：1825 × 1.6 = 2920。"""
        r = tools.law_match_penalty(CTX, "贪污", 96)
        self.assertEqual(r["sentence_days"], 2920)
        # 再高也不涨
        self.assertEqual(tools.law_match_penalty(CTX, "贪污", 100)["sentence_days"], 2920)

    def test_zero_sin_means_exempt(self):
        """sin=0（善恶簿上无实罪）→ 免刑。曾经给满刑期，是重罪误判的一半原因。"""
        r = tools.law_match_penalty(CTX, "盗窃", 0)
        self.assertEqual(r["sentence_days"], 0)
        self.assertIn("免刑", r["penalty_desc"])

    def test_empty_crime_means_exempt(self):
        r = tools.law_match_penalty(CTX, "", 80)
        self.assertEqual(r["sentence_days"], 0)

    def test_wuzui_means_exempt(self):
        r = tools.law_match_penalty(CTX, "无咎", 0)
        self.assertEqual(r["sentence_days"], 0)

    def test_composite_crime_falls_back_to_alias(self):
        """复合罪名整串查字典必然落空 —— 落空后若 days=0，谋财害命就判免刑。"""
        r = tools.law_match_penalty(CTX, "谋夺田产、逼死一家三口", 90)
        self.assertGreater(r["sentence_days"], 0, "复合罪名被判免刑，重罪漏判")
        self.assertEqual(r["matched"], "杀人")

    def test_alias_longest_match(self):
        """「盗伐」比「盗」具体，不能被前缀压过去。"""
        r = tools.law_match_penalty(CTX, "盗伐林木", 70)
        self.assertEqual(r["matched"], "盗窃")

    def test_penalty_desc_comes_from_dict(self):
        """判词描述取自 sys_dict.punishment，不是硬编码字符串。"""
        r = tools.law_match_penalty(CTX, "盗窃", 60)
        self.assertEqual(r["penalty_desc"], "刀山剑树，割裂罪身")


class MeritCalculateTest(unittest.TestCase):
    """功德分核算：同一关键词只记一次，先取基线再自首减半。"""

    def test_good_only(self):
        r = tools.merit_calculate(CTX, "施粥济贫", "")
        self.assertEqual(r["sin_score"], 0)
        self.assertGreater(r["merit_score"], 0)
        self.assertEqual(set(r["good_hits"]), {"济贫", "施粥"})

    def test_self_surrender_halves_sin(self):
        """斗殴30 + 杀100 = 130 → 自首减半 65 → clamp(100) 仍 65。

        注意顺序：代码是「求和 → 与基线取大 → 减半 → 最后才 clamp 到 100」，
        不是先 clamp 再减半。130 减半后本就低于 100，两者结果相同；
        但写成先 clamp 会得到 50，那是错的。
        """
        r = tools.merit_calculate(CTX, "斗殴误杀，事后自首", "")
        self.assertTrue(r["self_surrender"])
        self.assertEqual(r["sin_score"], 65.0)

    def test_baseline_taken_before_self_surrender(self):
        """顺序陷阱：必须先与基线取大，再减半。

        反例：先减半（35/2=17.5）再与基线 35 取大 → 35，自首白减。
        """
        r = tools.merit_calculate(CTX, "自首", "", merit_base=55, sin_base=35)
        self.assertEqual(r["sin_score"], 17.5, "自首被基线覆盖了，减罪失效")

    def test_baseline_floor(self):
        """文本里没提到罪行时，用善恶簿基线兜底。"""
        r = tools.merit_calculate(CTX, "", "", merit_base=55, sin_base=35)
        self.assertEqual(r["merit_score"], 55.0)
        self.assertEqual(r["sin_score"], 35.0)

    def test_clamp_to_100(self):
        r = tools.merit_calculate(CTX, "杀人 毙 致死 忤逆 贪污 侵吞", "")
        self.assertLessEqual(r["merit_score"], 100.0)
        self.assertLessEqual(r["sin_score"], 100.0)

    def test_same_keyword_counted_once(self):
        """「杀」出现三次只记一次 100 分，不能重复计数虚高。"""
        once = tools.merit_calculate(CTX, "杀", "")
        many = tools.merit_calculate(CTX, "杀 杀 杀 杀", "")
        self.assertEqual(once["sin_score"], many["sin_score"])


class BenevolentDeedNotPunishedTest(unittest.TestCase):
    """善行不能被算成罪业 —— 不变量 #1 的第一道防线。

    `bad` 表里有单字词条（伤 / 焚），做的是无上下文子串匹配。
    实测这四类善行全部被误判有罪，而它们都是最常见的善举描述：
        救死扶「伤」、行医济世疗「伤」 → sin 30
        「焚」香祷告、诵经「焚」香     → sin 50
    """

    BENIGN = [
        "救死扶伤，施药义诊",
        "焚香祷告，虔心礼佛",
        "为亡者超度，焚香诵经",
        "行医济世，疗伤治病",
        "舍身救人，奋不顾身",
        "拾金不昧，归还失主",
        "捐资修桥，施粥济贫",
        "孝养父母，厚葬亡者",
        "赈灾济民，收养孤儿",
        "教书育人，教化乡里",
        "放生池中鱼鳖",
        "见义勇为，救助落水儿童",
    ]

    def test_no_benign_deed_scores_sin(self):
        for text in self.BENIGN:
            with self.subTest(text=text):
                r = tools.merit_calculate(CTX, text, "")
                self.assertEqual(r["sin_score"], 0, f"善行被记了罪业：{text} → {r['bad_hits']}")

    def test_no_false_positive_in_bad_hits(self):
        """bad_hits 不能出现「伤/焚」这类被遮蔽掉的假阳性。"""
        for text in self.BENIGN:
            with self.subTest(text=text):
                r = tools.merit_calculate(CTX, text, "")
                for word in ("伤", "焚"):
                    self.assertNotIn(word, r["bad_hits"])

    def test_real_evil_still_punished(self):
        """遮蔽是精准的：真正的恶行不能因此漏判。"""
        cases = {
            "伤人致残": "伤",
            "焚毁屋舍，害及生灵": "焚",
            "斗殴伤人": "伤",
            "纵火焚烧民宅": "焚",
        }
        for text, word in cases.items():
            with self.subTest(text=text):
                r = tools.merit_calculate(CTX, text, "")
                self.assertGreater(r["sin_score"], 0, f"恶行漏判：{text}")
                self.assertIn(word, r["bad_hits"])


class RealmMatchTest(unittest.TestCase):
    """六道轮回判定 —— 全部是实测过的真实组合。"""

    def test_realms(self):
        cases = [
            (95, 0, "天道"),
            (90, 0, "天道"),
            (78, 60, "人道"),
            (69, 60, "阿修罗道"),
            (55, 0, "人道"),
            (50, 50, "阿修罗道"),
            (50, 10, "人道"),
            (30, 0, "畜生道"),
            (26, 100, "地狱道"),
            (20, 0, "饿鬼道"),
        ]
        for merit, sin, expected in cases:
            with self.subTest(merit=merit, sin=sin):
                self.assertEqual(tools.realm_match(CTX, merit, sin)["realm"], expected)

    def test_boundary_90(self):
        self.assertEqual(tools.realm_match(CTX, 89.9, 0)["realm"], "人道")

    def test_reason_mentions_both_scores(self):
        r = tools.realm_match(CTX, 78, 60)
        self.assertIn("78", r["realm_reason"])
        self.assertIn("60", r["realm_reason"])


class GoodPersonPipelineTest(unittest.TestCase):
    """端到端不变量：善人走完规则链，必须是「无咎 + 免刑 + 人道」。"""

    def test_good_person_full_rule_chain(self):
        text = "施粥济贫，修桥铺路，教化乡里，孝养父母，放生无数"
        merit = tools.merit_calculate(CTX, text, "")
        crime = tools.law_match_crime(CTX, text)
        penalty = tools.law_match_penalty(CTX, crime["crime"], sin_score=merit["sin_score"])
        realm = tools.realm_match(CTX, merit["merit_score"], merit["sin_score"])

        self.assertEqual(merit["sin_score"], 0)
        self.assertEqual(crime["crime"], "", "善人被定了罪")
        self.assertEqual(penalty["sentence_days"], 0, "善人被量刑")
        self.assertEqual(realm["realm"], "人道", "善人没进入人道")

    def test_real_offender_full_rule_chain(self):
        """吴承业真实案：谋夺田产、伪造地契、逼死一家三口。

        回归防线：这三类词（谋夺/夺产、逼死/害命）曾经在 law_match_crime 的
        别名表里完全缺失，只靠「伪造」命中 1.0 分，低于 2.0 阈值 → 空罪名
        → 免刑 → 谋财害命被判无罪释放。law_match_penalty 那边认识这些词，
        两张表不对齐就是这个后果。
        """
        text = "谋夺田产，伪造地契，逼死邻人一家三口"
        merit = tools.merit_calculate(CTX, text, "")
        crime = tools.law_match_crime(CTX, text)
        penalty = tools.law_match_penalty(CTX, crime["crime"], sin_score=merit["sin_score"])

        self.assertGreater(merit["sin_score"], 0, "重罪的罪业分为 0")
        self.assertNotEqual(crime["crime"], "", "真案没判出罪名")
        self.assertGreater(penalty["sentence_days"], 0, "真案被判免刑")

    def test_murder_by_proxy_words(self):
        """「逼死 / 害人性命」必须能判出杀人罪，不能因为没写「杀」字就漏。"""
        for text in ("逼死邻人", "害人性命", "逼死人命"):
            with self.subTest(text=text):
                r = tools.law_match_crime(CTX, text)
                self.assertEqual(r["crime"], "杀人", f"{text} 没判出杀人罪")

    def test_embezzlement_by_proxy_words(self):
        for text in ("挪用公帑", "侵夺田产", "贪墨赈灾银"):
            with self.subTest(text=text):
                r = tools.law_match_crime(CTX, text)
                self.assertEqual(r["crime"], "贪污", f"{text} 没判出贪污罪")

    def test_alias_tables_stay_aligned(self):
        """law_match_crime 与 law_match_penalty 的别名表必须同源。

        这条测试锁的是「维护时只改了一边」这个结构性风险：
        凡是 penalty 认得的词，crime 也必须认得，否则就会漏判。
        """
        import inspect
        src = inspect.getsource(tools)
        # penalty 侧的别名元组
        start = src.index('        "杀人": ("杀人", "害命"')
        end = src.index("    matched = crime if crime in base_table")
        penalty_block = src[start:end]
        # crime 侧的别名 dict
        cstart = src.index('        "杀人": {"杀": 2.5')
        cend = src.index("    haystack = text or \"\"")
        crime_block = src[cstart:cend]

        import re
        penalty_words = set(re.findall(r'"([^"]+)"', penalty_block))
        crime_words = set(re.findall(r'"([^"]+)"', crime_block))
        # 罪名键名本身两边都有，不算差异
        missing = {w for w in penalty_words - crime_words if w not in (
            "杀人", "伤人", "盗窃", "贪污", "欺诈", "忤逆", "邪淫", "诽谤", "逃税", "纵火"
        )}
        self.assertFalse(missing, f"penalty 认得但 crime 不认得的词（会导致漏判）：{sorted(missing)}")


class DictDrivenTest(unittest.TestCase):
    """业务规则来自 sys_dict —— 加罪名不用改代码。"""

    def test_add_crime_to_dict_takes_effect_immediately(self):
        db.insert("sys_dict", {
            "dict_type": "crime", "dict_key": "魇镇", "dict_value": "以巫术魇魅他人",
            "sort": 99, "remark": "测试用", "created_at": db.now(),
        })
        try:
            r = tools.law_match_crime(CTX, "以魇镇之术魇魅乡邻")
            self.assertEqual(r["crime"], "魇镇", "字典新增的罪名没生效，说明规则被硬编码了")
        finally:
            db.execute("DELETE FROM sys_dict WHERE dict_type='crime' AND dict_key='魇镇'")

    def test_punishment_dict_drives_desc(self):
        rows = db.all_rows("sys_dict", "dict_type='punishment'")
        keys = {r["dict_key"] for r in rows}
        self.assertIn("刀山狱", keys)
        self.assertEqual(len(rows), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
