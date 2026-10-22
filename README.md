# AnalystDesk

IIT Bombay Agentic AI Capstone Project.

A research assistant that produces research notes with verified numbers and supporting sources.

Suggested project structure:

project-name/
│
├── README.md                  # Project overview and setup instructions
├── requirements.txt           # Python dependencies
├── .env.example               # Required environment variables, without secrets
├── .gitignore                 # Files Git should ignore
├── LICENSE                    # How others may use the project
│
├── src/
│   └── project_name/
│       ├── __init__.py
│       ├── main.py            # Application entry point
│       ├── config.py          # Settings and environment variables
│       ├── state.py           # Shared LangGraph state
│       ├── graph.py           # Graph nodes, edges, and workflow
│       │
│       ├── agents/            # Agent logic
│       ├── tools/             # Search, calculations, and other tools
│       ├── prompts/           # Prompt templates
│       └── utils/             # Shared helper functions
│
├── data/
│   ├── sample/                # Small sample inputs
│   └── evaluation/            # Evaluation questions and expected results
│
├── tests/                     # Automated tests
│   ├── test_tools.py
│   └── test_graph.py
│
├── scripts/                   # Commands for setup and evaluation
│   └── evaluate.py
│
├── notebooks/                 # Experiments and exploration
│
└── docs/
    ├── architecture.md        # How the system works
    └── evaluation.md          # Evaluation approach and results


Your README.md should explain:
1. What the project does and the problem it solves.
2. Its main features and architecture.
3. How to install dependencies and configure API keys.
4. How to run it, with an example input and output.
5. How to run tests and evaluation.
6. Team members and their contributions.

Keep secrets and generated files out of Git. A starting .gitignore:
# Secrets
.env
.env.*
!.env.example

# Virtual environments
.venv/
venv/

# Python caches
__pycache__/
*.py[cod]
.pytest_cache/

# Generated output
outputs/
logs/

# Notebook checkpoints
.ipynb_checkpoints/