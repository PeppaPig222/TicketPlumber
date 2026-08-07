"""
Data Agent 插件
从 agents.data_agent 导入真实实现，作为 LazyAgentRegistry 可发现的插件暴露。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from agents.data_agent import DataAgent as BaseDataAgent


class DataAgent(BaseDataAgent):
    """数据一致性诊断 Agent（插件化入口）。"""
    pass
