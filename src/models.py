"""用于公告、人员、测试和评分页面的数据结构。"""
from dataclasses import dataclass


@dataclass
class Resource:
    course: dict
    id: str
    title: str
    summary: str
    text: str
    url: str
    timestamp: str = ""
