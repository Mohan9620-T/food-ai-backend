"""Strict agent actions: model output cannot select executables or write files."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectRepository(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=500)


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=8000)


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["search_code", "read_file", "list_files", "git_status", "git_diff", "finish"]
    argument: str = Field(default="", max_length=1000)
    answer: str = Field(default="", max_length=20000)
