"""
Operation Agent 插件
从 agents.operation_agent 导入真实实现，作为 LazyAgentRegistry 可发现的插件暴露。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from agents.operation_agent import OperationAgent as BaseOperationAgent


class OperationAgent(BaseOperationAgent):
    """操作与配置侧诊断 Agent（插件化入口）。"""
    pass
