import os
from typing import List
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Header, status
from pydantic import BaseModel
import pypdf

from app.db.vector_store import add_documents_to_vector_store
from app.api.v1.auth import get_current_user
from app.db.models import User

router = APIRouter(prefix="/upload", tags=["upload"])

def chunk_text(text: str, chunk_size: int = 600, chunk_overlap: int = 100) -> List[str]:
    chunks = []
    start = 0
    if not text:
        return chunks
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        # Break if we've reached the end
        if end >= len(text):
            break
        start += (chunk_size - chunk_overlap)
    return chunks

@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    x_gemini_api_key: str = Header(None, alias="X-Gemini-API-Key"),
    x_openai_api_key: str = Header(None, alias="X-OpenAI-API-Key")
):
    # Validate extension
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pdf", ".txt"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF and TXT files are accepted for custom knowledge RAG ingestion."
        )

    raw_text = ""
    try:
        if ext == ".pdf":
            # Read PDF pages
            reader = pypdf.PdfReader(file.file)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    raw_text += text + "\n"
        elif ext == ".txt":
            content = await file.read()
            raw_text = content.decode("utf-8")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse document: {str(e)}"
        )

    if not raw_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file contains no readable text."
        )

    # Chunk text
    chunks = chunk_text(raw_text)
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to segment text into processing chunks."
        )

    # Add chunks with metadata to vector store
    metadatas = [{"filename": filename, "user_id": current_user.id} for _ in chunks]
    
    # We pass the API key to generate vectors if present
    api_key = x_gemini_api_key or x_openai_api_key
    
    try:
        # In vector_store we will handle OpenAI vs Gemini embeddings generator
        add_documents_to_vector_store(chunks, metadatas=metadatas, api_key=api_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write embeddings to vector database: {str(e)}"
        )

    return {
        "status": "success",
        "filename": filename,
        "chunks_indexed": len(chunks),
        "total_characters": len(raw_text)
    }
