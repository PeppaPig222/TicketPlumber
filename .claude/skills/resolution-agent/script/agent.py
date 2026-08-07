"""
Resolution Agent 插件
从 agents.resolution_agent 导入真实实现，作为 LazyAgentRegistry 可发现的插件暴露。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from agents.resolution_agent import ResolutionAgent as BaseResolutionAgent


class ResolutionAgent(BaseResolutionAgent):
    """交叉验证与归属判定 Agent（插件化入口）。"""
    pass
