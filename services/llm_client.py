"""
LLMClient — 大模型调用客户端
支持 DeepSeek API（OpenAI 兼容协议）
用于概念提取、RAG 生成、记忆整合
"""
import logging
from typing import Optional

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 调用封装"""

    def __init__(self):
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self.model = config.llm_model
        logger.info(f"LLMClient 已连接: {config.llm_base_url}, model={self.model}")

    def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ):
        """发送聊天请求"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature or config.llm_temperature,
                max_tokens=max_tokens or config.llm_max_tokens,
                stream=stream,
            )
            return response
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    def stream_chat(self, messages: list[dict], temperature: Optional[float] = None):
        """流式聊天"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature or config.llm_temperature,
            max_tokens=config.llm_max_tokens,
            stream=True,
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def extract_concepts(self, text: str, max_concepts: int = 10) -> dict:
        """
        Karpathy 风格概念提取
        从文本中识别关键概念，并输出 JSON 格式
        """
        prompt = f"""你是一个知识提取引擎。请从以下文本中提取关键概念（实体、术语、方法论、框架等）。

要求:
1. 最多提取 {max_concepts} 个概念
2. 每个概念包含: name(名称), description(简短描述), category(mcn_business/agent_memory/personal/technical)
3. 识别概念之间的关系: 概念A relation_type 概念B
4. relation_type 可选值: depends_on, part_of, related_to, example_of

文本:
{text[:3000]}

请严格按 JSON 格式输出:
{{
  "concepts": [
    {{"name": "概念名", "description": "描述", "category": "分类"}}
  ],
  "edges": [
    {{"source": "概念A", "target": "概念B", "relation_type": "关系类型", "evidence": "来源句"}}
  ]
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是专业的知识管理助手，擅长提取结构化概念。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            import json
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f"概念提取失败: {e}")
            return {"concepts": [], "edges": []}

    def generate_summary(self, texts: list[str], period: str = "daily") -> str:
        """生成记忆整合摘要（GBrain 风格）"""
        joined = "\n---\n".join(texts)
        prompt = f"""你是一个"第二大脑"记忆整合引擎。请对以下{period}知识片段进行整合，生成结构化摘要。

要求:
1. 总结关键信息和洞察
2. 识别知识之间的关联
3. 标注重要程度（高/中/低）
4. 用中文输出，简洁专业

{period}知识片段:
{joined[:6000]}

输出格式:
## 关键洞察
- ...

## 知识关联
- ...

## 待深入方向
- ..."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是知识管理专家，擅长信息整合和洞察提炼。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"记忆整合失败: {e}")
            return f"整合失败: {e}"
