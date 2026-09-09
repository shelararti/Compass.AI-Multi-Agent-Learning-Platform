"""
Builds the tutor agent graph:

                          Student
                             |
                        Orchestrator
                 (stage="generate")   (stage="evaluate")
                 /       |        \\             |
            Teacher    Quiz     Coding    Evaluation Agent
              |          |         |             |
              END       END       END      Memory (Progress DB)
                                                  |
                                          Learning Planner
                                                  |
                                                 END

One graph, two entry "stages":
- stage="generate": orchestrator resolves topic + routes to a specialist,
  which produces a chat reply / quiz / coding task for the student.
- stage="evaluate": orchestrator hands straight to the Evaluation Agent,
  which grades what the student submitted, then Memory persists it and
  the Planner suggests what to study next.

"progress" intent (no new submission, just "how am I doing") skips
evaluation and memory-writing, going straight from the orchestrator to
the Planner.
"""

from langgraph.graph import StateGraph, START, END

from .state import TutorState
from .agents.orchestrator import orchestrator_node, route_after_orchestrator
from .agents.teacher import teacher_node
from .agents.quiz import quiz_node
from .agents.coding import coding_node
from .agents.evaluation import evaluation_node
from .agents.memory_update import memory_update_node
from .agents.planner import planner_node


def build_graph():
    builder = StateGraph(TutorState)

    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("teacher_agent", teacher_node)
    builder.add_node("quiz_agent", quiz_node)
    builder.add_node("coding_agent", coding_node)
    builder.add_node("evaluation_agent", evaluation_node)
    builder.add_node("memory_update", memory_update_node)
    builder.add_node("planner_agent", planner_node)

    builder.add_edge(START, "orchestrator")

    builder.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "teacher_agent": "teacher_agent",
            "quiz_agent": "quiz_agent",
            "coding_agent": "coding_agent",
            "evaluation_agent": "evaluation_agent",
            "planner_agent": "planner_agent",
            "end": END,
        },
    )

    # Generation specialists return directly to the student.
    builder.add_edge("teacher_agent", END)
    builder.add_edge("quiz_agent", END)
    builder.add_edge("coding_agent", END)

    # Evaluation flows through memory persistence, then the planner,
    # matching the Evaluation -> Progress/Memory DB -> Planner chain.
    builder.add_edge("evaluation_agent", "memory_update")
    builder.add_edge("memory_update", "planner_agent")
    builder.add_edge("planner_agent", END)

    return builder.compile()


# Compiled once at import time; FastAPI will import this singleton.
tutor_graph = build_graph()
