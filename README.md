# QueryMind

<div align="center">

**Intelligent Database Querying Through Natural Language**

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-orange.svg)](https://ollama.ai/)
[![License](https://img.shields.io/badge/License-Academic-purple.svg)](#license)

*A secure, on-premise text-to-SQL system that converts natural language questions into SQL queries using RAG and local LLMs.*

</div>

---

## Problem Statement

Traditional database querying requires knowledge of SQL syntax, schema structure, and table relationships. This creates barriers for:

- **Non-technical business users** who need quick data insights
- **Data analysts** without deep SQL expertise  
- **Executives** requiring fast decision-making data
- **Organizations** that cannot expose data to external AI APIs due to privacy and compliance

**QueryMind** eliminates these barriers by allowing users to ask questions in plain English while keeping all data processing 100% local.

---

## Features

| Feature | Description |
|---------|-------------|
| **Natural Language Interface** | Ask questions like "Show customers in Bahrain" or "Count orders per customer" |
| **RAG-Enhanced Accuracy** | Retrieves relevant schema context to improve query generation |
| **Read-Only Security** | Only SELECT queries permitted; blocks INSERT, UPDATE, DELETE, DROP |
| **100% Local Processing** | No data sent to external APIs; runs entirely on your machine |
| **Session Isolation** | Each user gets isolated vector stores for their schema |
| **Schema Explorer** | Click tables to view column metadata, types, and keys |
| **Performance Metrics** | Track RAG retrieval, LLM generation, and execution times |
| **Multi-Database Support** | Connect to local or remote MySQL/MariaDB databases |

---

## Architecture

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

## Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Flask (Python) |
| **Database** | MySQL / MariaDB |
| **LLM** | Llama 3.1:8b via Ollama |
| **Vector DB** | ChromaDB |
| **Embeddings** | mxbai-embed-large |
| **Frontend** | HTML5, CSS3, JavaScript |

---

## Installation

### Prerequisites

Before starting, make sure you have the following installed:

- **Python 3.8 or higher**
- **Ollama** - For running local LLMs
- **MySQL or MariaDB** - Database server

---

### Step 1: Set Up the Database

#### For Linux (Using MariaDB)

1. Install MariaDB:
```bash
sudo apt update
sudo apt install mariadb-server
```

2. Start the MariaDB service:
```bash
sudo systemctl start mariadb
sudo systemctl enable mariadb
```

3. Secure your installation:
```bash
sudo mysql_secure_installation
```

4. Log in and create a database:
```bash
sudo mysql -u root -p
```

```sql
CREATE DATABASE your_database_name;
CREATE USER 'your_username'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON your_database_name.* TO 'your_username'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

5. Import your data (if you have a SQL file):
```bash
mysql -u your_username -p your_database_name < your_data.sql
```

#### For Windows (Using XAMPP)

1. Download and install [XAMPP](https://www.apachefriends.org/)

2. Open XAMPP Control Panel and start **MySQL**

3. Click **Admin** next to MySQL to open phpMyAdmin

4. Create a new database:
   - Click "New" in the left sidebar
   - Enter your database name and click "Create"

5. Import your data:
   - Select your database
   - Click "Import" tab
   - Choose your SQL file and click "Go"

---

### Step 2: Install Ollama

#### For Linux
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

#### For Windows
Download and install from [ollama.ai](https://ollama.ai/)

#### Verify Ollama is running
```bash
ollama --version
```

---

### Step 3: Download Required Models

Pull the LLM and embedding models:

```bash
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

This may take a few minutes depending on your internet connection.

---

### Step 4: Clone and Set Up the Project

1. Clone the repository:
```bash
git clone https://github.com/malmaraghi/QueryMind.git
cd QueryMind
```

2. Create a virtual environment:
```bash
python -m venv venv
```

3. Activate the virtual environment:

**Linux/macOS:**
```bash
source venv/bin/activate
```

**Windows:**
```bash
venv\Scripts\activate
```

4. Install Python dependencies:
```bash
pip install -r requirements.txt
```

---

### Step 5: Run the Application

1. Make sure Ollama is running in the background

2. Start the Flask application:
```bash
python app.py
```

3. Open your browser and go to:
```
http://localhost:5000
```

4. Log in with your database credentials:
   - **Host**: `localhost` (or your database server IP)
   - **Port**: `3306` (default MySQL/MariaDB port)
   - **Username**: Your database username
   - **Password**: Your database password
   - **Database**: Your database name

---

## Usage

1. **Login** - Enter your database credentials
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

---

## Project Structure

```
QueryMind/
├── app.py                 # Main Flask web application
├── chroma_rag.py          # RAG indexing and retrieval logic
├── llm_engine.py          # LLM prompt engineering and inference
├── query_executor.py      # SQL extraction, validation, and execution
├── schema_loader.py       # Database schema extraction (testing)
├── db_config.py           # Database config (testing only)
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

> **Note:** The files `db_config.py`, `schema_loader.py`, and `main.py` are for CLI testing purposes only. The main application uses `app.py` which handles database connections through the web interface.

---

## Security

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

## Experimental Results

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

## Configuration

### Models Used

The application uses the following models by default:

- **LLM**: `llama3.1:8b` - For SQL query generation
- **Embedding Model**: `mxbai-embed-large` - For RAG schema retrieval

To change models, edit the configuration in `app.py`:

```python
MAIN_LLM_MODEL = "llama3.1:8b"
EMBEDDING_MODEL = "mxbai-embed-large:latest"
```

---

## License

This project was developed as a senior project at the **University of Bahrain**, College of Information Technology.

---

## Authors

**Cyber Security Students - Batch 2025**

- Mohamed Almaraghi
- Abdulrahman Bumeajib
- Ali Alsowaidi

University of Bahrain

---

## Acknowledgments

- [Ollama](https://ollama.ai/) - Local LLM inference
- [ChromaDB](https://www.trychroma.com/) - Vector database
- [Flask](https://flask.palletsprojects.com/) - Web framework
- [MariaDB](https://mariadb.org/) - Database system

---

## Full Project Report

Download the full project report (PDF): [Download full report (PDF)](https://drive.google.com/file/d/1ng_RuWAV661o0JxJE2x5vGtIlHZuM9De/view?usp=drive_link)

---

<div align="center">

**University of Bahrain - Senior Project 2025**

</div>
