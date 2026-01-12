# QueryMind

**Natural language to SQL query system using local LLMs**

Convert questions in plain English to SQL queries while keeping all data 100% local and secure.

For more information, visit our website: [querymind-text2sql.com](https://querymind-text2sql.com)

---

## Setup Instructions

### Prerequisites

- Python 3.8+
- MySQL or MariaDB
- Ollama

---

### 1. Install Database

#### Linux
```bash
sudo apt update
sudo apt install mariadb-server
sudo systemctl start mariadb
sudo mysql_secure_installation
```

#### Windows
1. Download [XAMPP](https://www.apachefriends.org/)
2. Install and start MySQL from XAMPP Control Panel

---

### 2. Install Ollama

#### Linux
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

#### Windows
Download from [ollama.ai](https://ollama.ai/)

#### Download Models
```bash
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

---

### 3. Setup Project

```bash
git clone https://github.com/malmaraghi/QueryMind.git
cd QueryMind
python -m venv venv
```

#### Activate Virtual Environment

**Linux:**
```bash
source venv/bin/activate
```

**Windows:**
```bash
venv\Scripts\activate
```

#### Install Dependencies
```bash
pip install -r requirements.txt
```

---

### 4. Run Application

```bash
python app.py
```

Open browser to `http://localhost:5000`

---

## Usage

1. Enter your database credentials in the login page
2. Ask questions in plain English
3. Review the generated SQL query
4. View results in a table format

---

## Project Structure

```
QueryMind/
├── app.py                 # Main Flask web application
├── config.py              # Model configuration (LLM & Embedding)
├── chroma_rag.py          # RAG indexing and retrieval
├── llm_engine.py          # LLM prompt engineering
├── query_executor.py      # SQL validation and execution
├── requirements.txt       # Python dependencies
├── templates/
│   ├── login.html
│   ├── home.html
│   └── result.html
├── static/
│   └── style.css
├── testing/               # CLI testing files
│   ├── db_config.py
│   ├── schema_loader.py
│   └── main.py
├── experiment/            # Model comparison experiments
│   ├── exp_comp.py
│   ├── aggregate_results.py
│   ├── gold_questions.json
│   └── run_experiments.sh
└── exp_com_res/           # Experiment results
    ├── results_*.json
    └── summary_report.json
```

---

## License

Graduation project - University of Bahrain, College of Information Technology (2025)

---

## Authors

Mohamed Almaraghi, Abdulrahman Bumeajib, Ali Alsowaidi

