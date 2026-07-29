import pytest
from langgraph.graph import END, START, StateGraph

from web_task_agent.agent_checkpoint import open_sqlite_checkpointer


def increment(state: dict[str, int]) -> dict[str, int]:
    return {"count": state["count"] + 1}


def build_counter_graph(checkpointer):
    graph = StateGraph(dict)
    graph.add_node("increment", increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_checkpointer_creates_parent_and_survives_reopen(tmp_path):
    path = tmp_path / "nested" / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "thread-1"}}

    async with open_sqlite_checkpointer(path) as first:
        await build_counter_graph(first).ainvoke({"count": 0}, config=config)

    async with open_sqlite_checkpointer(path) as second:
        snapshot = await build_counter_graph(second).aget_state(config)

    assert path.exists()
    assert snapshot.values["count"] == 1

    path.unlink()
    assert not path.exists()


@pytest.mark.asyncio
async def test_checkpointer_rejects_path_without_file_name(tmp_path):
    with pytest.raises(ValueError, match="must name a file"):
        async with open_sqlite_checkpointer(tmp_path / ".."):
            raise AssertionError("invalid checkpoint path was opened")
