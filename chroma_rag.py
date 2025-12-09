import re
from typing import List
import shutil
import os
import tempfile

import chromadb
import ollama


def chunk_schema(schema_text: str):
    pattern = re.compile(
        r"CREATE TABLE\s+`?(\w+)`?\s*\((.*?)\)[^;]*;",
        re.IGNORECASE | re.DOTALL
    )
    chunks = []
    for match in pattern.finditer(schema_text):
        table_name = match.group(1)
        statement_full = match.group(0).strip()
        chunks.append({"name": table_name, "content": statement_full})
    
    if not chunks and schema_text:
        chunks.append({"name": "full_schema", "content": schema_text})
    
    return chunks


def embed_texts(texts: List[str], model: str) -> List[List[float]]:
    response = ollama.embed(model=model, input=texts)
    return response["embeddings"]


def index_schema_in_chroma(schema_text: str, persist_path: str, model: str):
    # Clear existing ChromaDB data for this path to avoid conflicts
    if os.path.exists(persist_path):
        try:
            shutil.rmtree(persist_path)
            print(f"Cleared old ChromaDB at {persist_path}")
        except Exception as e:
            print(f"Warning: Could not clear {persist_path}: {e}")
    
    # Ensure directory exists with proper permissions
    os.makedirs(persist_path, mode=0o755, exist_ok=True)
    
    try:
        chroma_client = chromadb.PersistentClient(path=persist_path)
        
        # Try to delete existing collection (ignore if doesn't exist)
        try:
            existing_collections = chroma_client.list_collections()
            for col in existing_collections:
                if col.name == "db_schema":
                    chroma_client.delete_collection(name="db_schema")
                    print("Deleted existing db_schema collection")
        except Exception as e:
            print(f"Note: {e}")
        
        # Create fresh collection
        collection = chroma_client.create_collection(
            name="db_schema",
            metadata={"hnsw:space": "cosine"}
        )

        chunks = chunk_schema(schema_text)
        print("Table chunks found:")
        for chunk in chunks:
            print(chunk["name"])
        
        if not chunks:
            print("Warning: No schema chunks found")
            return
        
        texts = [chunk["content"] for chunk in chunks]
        embeddings = embed_texts(texts, model=model)

        for idx, chunk in enumerate(chunks):
            collection.add(
                ids=[str(idx)],
                documents=[chunk["content"]],
                metadatas=[{"table_name": chunk["name"]}],
                embeddings=[embeddings[idx]],
            )
        
        print(f"✓ Schema indexed: {len(chunks)} tables.")
        
    except Exception as e:
        print(f"Error in index_schema_in_chroma: {e}")
        # Don't raise - allow app to continue
        pass


def retrieve_schema_context(question: str, top_k: int = 5, persist_path: str = "./chroma_db", model: str = None):
    """Retrieve relevant schema context for a question.
    
    Args:
        question: The user's natural language question
        top_k: Number of top relevant tables to retrieve (default 5 for better JOIN support)
        persist_path: Path to ChromaDB persistence directory
        model: Embedding model name
    
    Returns:
        String containing relevant CREATE TABLE statements
    """
    if model is None:
        raise ValueError("You must pass an embedding model for RAG retrieval.")
    
    try:
        chroma_client = chromadb.PersistentClient(path=persist_path)
        
        # Check if collection exists
        try:
            collection = chroma_client.get_collection("db_schema")
        except Exception:
            print(f"Collection 'db_schema' not found at {persist_path}. Schema needs to be re-indexed.")
            return "ERROR: Schema not indexed. Please log out and log in again to re-index the database schema."

        # Check if collection has documents
        if collection.count() == 0:
            print(f"Collection 'db_schema' is empty at {persist_path}. Schema needs to be re-indexed.")
            return "ERROR: Schema not indexed. Please log out and log in again to re-index the database schema."

        question_emb = embed_texts([question], model=model)[0]
        results = collection.query(
            query_embeddings=[question_emb],
            n_results=top_k,
            include=["documents", "metadatas"]
        )
        
        context_list = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            context_list.append(f"-- Table: {meta['table_name']}\n{doc}")
        
        if not context_list:
            return "No relevant tables found in the database schema."
        
        return "\n\n".join(context_list)
    
    except Exception as e:
        print(f"Error in retrieve_schema_context: {e}")
        return f"Error retrieving schema context: {str(e)}"