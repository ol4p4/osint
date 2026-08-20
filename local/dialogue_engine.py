import json, os, re, yaml, uuid
from pathlib import Path
from datetime import datetime, timezone

QUESTION_ROUNDS = [
    {"round": 1, "name": "WHAT", "prompt": "你说的[观点]具体指什么？请从以下维度中选择或补充：经济结构、社会政策、制度设计、文化传统、人口趋势、国际关系。"},
    {"round": 2, "name": "WHY", "prompt": "你基于什么得出这个判断？是读过什么书、看过什么数据、还是个人观察和经历？请具体说明。"},
    {"round": 3, "name": "HOW", "prompt": "这个观点如何验证？什么数据出现能支持它？什么数据出现能反驳它？请给出具体的可观察指标。"},
    {"round": 4, "name": "WHEN", "prompt": "你认为这个判断的时间跨度是多长？什么时候能验证对错？给出一个到期日。"},
    {"round": 5, "name": "WHO", "prompt": "这个判断对谁影响最大？对你个人意味着什么？你会因此做出什么改变？"},
]

class DialogueEngine:
    def __init__(self, config, idea_dir):
        self.config = config
        self.idea_dir = Path(idea_dir)
        self.idea_dir.mkdir(parents=True, exist_ok=True)
        self.dialogue_dir = Path(os.getenv("OUTPUT_DIR", r"D:\\Codex输出\\osint_卫星图")) / "dialogues"
        self.dialogue_dir.mkdir(parents=True, exist_ok=True)
