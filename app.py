import os
import time
import mariadb

from flask import Flask, render_template, request, session, redirect, url_for
from chroma_rag import index_schema_in_chroma, retrieve_schema_context
from llm_engine import ask_llm
from query_executor import extract_sql, run_query

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret_change_in_production")

MAIN_LLM_MODEL = "llama3.1:8b"
EMBEDDING_MODEL = "mxbai-embed-large:latest"


def connect_to_db():
    """Connect using session credentials"""
    if not all(k in session for k in ['db_host', 'db_user', 'db_password', 'db_name', 'db_port']):
        return None
    
    try:
        return mariadb.connect(
            host=session['db_host'],
            port=int(session['db_port']),
            user=session['db_user'],
            password=session['db_password'],
            database=session['db_name']
        )
    except Exception as e:
        print(f"Connection error: {e}")
        return None


def load_schema_from_session():
    """Load schema using session credentials"""
    conn = connect_to_db()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES;")
        tables = [row[0] for row in cursor.fetchall()]

        schema_text = ""
        for table in tables:
            cursor.execute(f"SHOW CREATE TABLE `{table}`;")
            result = cursor.fetchone()
            if result and len(result) > 1:
                schema_text += f"{result[1]};\n\n"
        
        cursor.close()
        conn.close()
        return schema_text, tables
    except Exception as error:
        print(f"Schema load error: {error}")
        return None, []


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    
    if request.method == "POST":
        db_host = request.form.get("db_host", "localhost").strip()
        db_port = request.form.get("db_port", "3306").strip()
        db_user = request.form.get("db_user", "").strip()
        db_password = request.form.get("db_password", "")
        db_name = request.form.get("db_name", "").strip()
        
        # Validate inputs
        if not all([db_user, db_name]):
            error = "Invalid database credentials. Please check your information and try again."
            return render_template("login.html", error=error)
        
        # Test connection
        try:
            test_conn = mariadb.connect(
                host=db_host,
                port=int(db_port),
                user=db_user,
                password=db_password,
                database=db_name
            )
            test_conn.close()
            
            # Store in session
            session['db_host'] = db_host
            session['db_port'] = db_port
            session['db_user'] = db_user
            session['db_password'] = db_password
            session['db_name'] = db_name
            session['logged_in'] = True
            
            # Index schema with unique absolute path per user
            schema_text, tables = load_schema_from_session()
            if schema_text:
                # Use unique path for each user/database combination
                import hashlib
                unique_id = hashlib.md5(f"{db_user}_{db_host}_{db_name}".encode()).hexdigest()[:8]
                base_dir = os.path.expanduser("~/.querymind_chromadb")
                persist_path = os.path.join(base_dir, unique_id)
                session['persist_path'] = persist_path
                
                print(f"Indexing schema at: {persist_path}")
                try:
                    index_schema_in_chroma(schema_text, persist_path=persist_path, model=EMBEDDING_MODEL)
                except Exception as e:
                    print(f"ChromaDB indexing error (non-fatal): {e}")
                    # Continue anyway - the error is handled in chroma_rag.py
            
            return redirect(url_for('home'))
            
        except mariadb.Error as e:
            # Generic error message for security - don't reveal specific details
            error = "Invalid database credentials. Please check your information and try again."
            print(f"Login failed: {str(e)}")  # Log actual error server-side only
            return render_template("login.html", error=error)
        except Exception as e:
            # Generic error for any other issues
            error = "Connection failed. Please verify your credentials and try again."
            print(f"Login error: {e}")  # Log actual error server-side only
            return render_template("login.html", error=error)
    
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route("/api/table/<table_name>")
def get_table_metadata(table_name):
    """API endpoint to get table metadata (columns, types, keys, etc.)"""
    if not session.get('logged_in'):
        return {"error": "Not authenticated"}, 401
    
    conn = connect_to_db()
    if not conn:
        return {"error": "Could not connect to database"}, 500
    
    try:
        cursor = conn.cursor()
        # Get column information
        cursor.execute(f"DESCRIBE `{table_name}`;")
        columns = cursor.fetchall()
        
        metadata = []
        for col in columns:
            metadata.append({
                "column": col[0],
                "type": col[1],
                "null": col[2],
                "key": col[3] if col[3] else "-",
                "default": str(col[4]) if col[4] is not None else "-"
            })
        
        cursor.close()
        conn.close()
        return {"table": table_name, "columns": metadata}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/", methods=["GET", "POST"])
def home():
    # Check if logged in
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    error = ""
    user_input = ""
    
    # Get accessible tables and schema
    schema_text, accessible_tables = load_schema_from_session()
    
    if request.method == "POST":
        user_input = request.form.get("user_input", "").strip()

        time_start_rag = time.time()
        rag_context = retrieve_schema_context(
            user_input, 
            persist_path=session['persist_path'], 
            model=EMBEDDING_MODEL
        )
        
        # If schema not indexed, re-index automatically
        if rag_context.startswith("ERROR:") and schema_text:
            print("Schema not indexed, re-indexing now...")
            try:
                index_schema_in_chroma(schema_text, persist_path=session['persist_path'], model=EMBEDDING_MODEL)
                # Retry retrieval after re-indexing
                rag_context = retrieve_schema_context(
                    user_input, 
                    persist_path=session['persist_path'], 
                    model=EMBEDDING_MODEL
                )
            except Exception as e:
                print(f"Re-indexing failed: {e}")
        
        time_end_rag = time.time()
        
        time_start_llm = time.time()
        llm_output = ask_llm(user_input, rag_context, model_name=MAIN_LLM_MODEL)
        time_end_llm = time.time()
        
        sql_query = extract_sql(llm_output)
        
        time_rag = round(time_end_rag - time_start_rag, 3)
        time_llm = round(time_end_llm - time_start_llm, 3)
        time_total_generation = round(time_rag + time_llm, 3)

        if sql_query.startswith("Error:"):
            error = sql_query
            return render_template(
                "home.html",
                user_input=user_input,
                error=error,
                db_name=session['db_name'],
                db_user=session['db_user'],
                db_host=session['db_host'],
                db_port=session['db_port'],
                tables=accessible_tables
            )

        conn = connect_to_db()
        if not conn:
            error = "Could not connect to the database."
            return render_template(
                "home.html",
                user_input=user_input,
                error=error,
                db_name=session['db_name'],
                db_user=session['db_user'],
                db_host=session['db_host'],
                db_port=session['db_port'],
                tables=accessible_tables
            )
        
        try:
            time_start_exec = time.time()
            results = run_query(sql_query, conn)
            time_end_exec = time.time()
            time_execution = round(time_end_exec - time_start_exec, 3)
        except Exception as e:
            error = f"Error executing query: {str(e)}"
            results = ""
            time_execution = 0
        finally:
            conn.close()

        return render_template(
            "result.html",
            user_input=user_input,
            sql_query=sql_query,
            results=results,
            error=error,
            db_name=session['db_name'],
            db_user=session['db_user'],
            db_host=session['db_host'],
            db_port=session['db_port'],
            time_rag=time_rag,
            time_llm=time_llm,
            time_generation=time_total_generation,
            time_execution=time_execution
        )

    return render_template(
        "home.html",
        user_input=user_input,
        error=error,
        db_name=session['db_name'],
        db_user=session['db_user'],
        db_host=session['db_host'],
        db_port=session['db_port'],
        tables=accessible_tables
    )


if __name__ == "__main__":
    app.run(debug=True)