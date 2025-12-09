si# QueryMind

<div align="center">

**Intelligent Database Querying Through Natural Language**

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-orange.svg)](https://ollama.ai/)
[![License](https://img.shields.io/badge/License-Academic-purple.svg)](#license)

*A secure, on-premise text-to-SQL system that converts natural language questions into SQL queries using RAG and local LLMs.*

</div>

---

## 🎯 Problem Statement

Traditional database querying requires knowledge of SQL syntax, schema structure, and table relationships. This creates barriers for:

- **Non-technical business users** who need quick data insights
- **Data analysts** without deep SQL expertise  
- **Executives** requiring fast decision-making data
- **Organizations** that cannot expose data to external AI APIs due to privacy and compliance

**QueryMind** eliminates these barriers by allowing users to ask questions in plain English while keeping all data processing 100% local.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🗣️ **Natural Language Interface** | Ask questions like "Show customers in Bahrain" or "Count orders per customer" |
| 🎯 **RAG-Enhanced Accuracy** | Retrieves relevant schema context to improve query generation |
| 🔒 **Read-Only Security** | Only SELECT queries permitted; blocks INSERT, UPDATE, DELETE, DROP |
| 🏠 **100% Local Processing** | No data sent to external APIs; runs entirely on your machine |
| 🔐 **Session Isolation** | Each user gets isolated vector stores for their schema |
| 📊 **Schema Explorer** | Click tables to view column metadata, types, and keys |
| ⚡ **Performance Metrics** | Track RAG retrieval, LLM generation, and execution times |
| 🌐 **Multi-Database Support** | Connect to local or remote MySQL/MariaDB databases |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                           │
│                    (Flask Web Application)                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Query Processing                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Input     │  │    RAG      │  │      SQL Validator      │  │
│  │ Validator   │──│  Retrieval  │──│  (SELECT-only check)    │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  ChromaDB   │  │   Ollama    │  │   MariaDB   │
│ (Embeddings)│  │   (LLM)     │  │ (Database)  │
└─────────────┘  └─────────────┘  └─────────────┘
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Flask (Python) |
| **Database** | MySQL / MariaDB |
| **LLM** | Llama 3.1:8b via Ollama |
| **Vector DB** | ChromaDB |
| **Embeddings** | mxbai-embed-large |
| **Frontend** | HTML5, CSS3, JavaScript |

---

## 📦 Installation

### Prerequisites

- Python 3.8+
- [Ollama](https://ollama.ai/) installed and running
- MySQL or MariaDB database

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/querymind.git
cd querymind
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Pull Required Ollama Models

```bash
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

### 5. Run the Application

```bash
python app.py
```

### 6. Access the Application

Open your browser and navigate to:
```
http://localhost:5000
```

---

## 🚀 Usage

### Web Application (Recommended)

1. **Login** - Enter your database credentials (host, port, username, password, database name)
2. **Explore Schema** - Click on tables in the sidebar to view column details
3. **Ask Questions** - Type natural language questions in the input box
4. **Review SQL** - See the generated SQL query before execution
5. **View Results** - Results displayed in a formatted table

### Example Queries

```
Show all customers from France
```
```
List products with price greater than 50
```
```
Count orders per customer with customer name
```
```
Show total revenue by product category
```

### CLI Testing (Development)

For development and testing purposes, you can use the CLI interface:

1. Configure your database credentials in `db_config.py`
2. Run:
```bash
python main.py
```

> **Note:** The CLI is for testing only. The web application (`app.py`) is the main interface with full functionality.

---

## 📁 Project Structure

```
querymind/
├── app.py                 # Main Flask web application
├── chroma_rag.py          # RAG indexing and retrieval logic
├── llm_engine.py          # LLM prompt engineering and inference
├── query_executor.py      # SQL extraction, validation, and execution
├── schema_loader.py       # Database schema extraction
├── db_config.py           # Database config (CLI testing only)
├── main.py                # CLI interface (testing only)
├── requirements.txt       # Python dependencies
├── templates/
│   ├── login.html         # Login page
│   ├── home.html          # Main query interface
│   └── result.html        # Results display
├── static/
│   └── style.css          # Application styles
└── experiment/
    ├── exp_comp.py        # Model comparison experiments
    ├── aggregate_results.py
    ├── gold_questions.json
    └── run_experiments.sh
```

---

## 🔒 Security

QueryMind implements multiple security layers:

| Layer | Protection |
|-------|------------|
| **Input Validation** | Blocks dangerous keywords (DROP, DELETE, INSERT, etc.) before LLM processing |
| **SQL Validation** | Ensures only SELECT queries are executed |
| **Session Isolation** | Separate vector stores per user/database connection |
| **Safe Error Messages** | Generic errors prevent information disclosure |
| **Schema-Only Exposure** | LLM sees only table structure, never actual data |
| **Local Processing** | No data transmitted to external services |

---

## 📊 Experimental Results

We evaluated QueryMind with different model configurations:

| Model Configuration | Accuracy | Avg Generation Time |
|---------------------|----------|---------------------|
| **Llama 3.1:8b + mxbai-embed-large** | **100%** | 28.97s |
| Qwen3:1.7b + mxbai-embed-large | 60% | 6.69s |
| Gemma3:1b + mxbai-embed-large | 60% | 5.19s |
| Qwen3:1.7b + all-minilm | 60% | 4.52s |
| Gemma3:1b + all-minilm | 40% | 4.20s |

### Key Findings

- **Larger models** achieve higher accuracy but require more generation time
- **Smaller models** are faster but less accurate for complex queries
- **Embedding model quality** (mxbai vs all-minilm) impacts retrieval accuracy
- All configurations pass security validation tests

---

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret key | `supersecret_change_in_production` |

### Model Configuration

Edit `app.py` to change models:

```python
MAIN_LLM_MODEL = "llama3.1:8b"              # LLM for SQL generation
EMBEDDING_MODEL = "mxbai-embed-large:latest" # Embedding model for RAG
```

### Supported Models

| LLM Models | Embedding Models |
|------------|------------------|
| llama3.1:8b | mxbai-embed-large |
| qwen3:1.7b | all-minilm |
| gemma3:1b | nomic-embed-text |

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

This project was developed as a senior project at the **University of Bahrain**, College of Information Technology.

---

## 👥 Authors

- **Mohamed Almaraghi**
- **Abdulrahman Bumeajib**
- **Ali Alsowaidi**

University of Bahrain, 2025

---

## 🙏 Acknowledgments

- [Ollama](https://ollama.ai/) - Local LLM inference
- [ChromaDB](https://www.trychroma.com/) - Vector database
- [Flask](https://flask.palletsprojects.com/) - Web framework
- [MariaDB](https://mariadb.org/) - Database system

---

<div align="center">

**Made with ❤️ at University of Bahrain**

</div>
