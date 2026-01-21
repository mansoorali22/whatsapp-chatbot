"""
RAG (Retrieval-Augmented Generation) service for answering questions from the book
"""
import logging
from typing import List, Dict, Any, Optional
import openai
from openai import AsyncOpenAI

from db.connection import db
from app.config import settings

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.embedding_model = settings.OPENAI_EMBEDDING_MODEL
    
    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text"""
        try:
            response = await self.client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        
        except Exception as e:
            logger.error(f"Error generating embedding: {e}", exc_info=True)
            raise
    
    async def retrieve_relevant_chunks(
        self,
        query: str,
        top_k: int = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant book chunks using vector similarity search
        
        Args:
            query: User's question
            top_k: Number of chunks to retrieve
        
        Returns:
            List of relevant chunks with metadata
        """
        if top_k is None:
            top_k = settings.RETRIEVAL_TOP_K
        
        try:
            # Generate query embedding
            query_embedding = await self.get_embedding(query)
            
            # Search for similar chunks using cosine similarity
            search_query = """
                SELECT 
                    id,
                    chunk_text,
                    chapter_title,
                    section_title,
                    page_number,
                    chunk_index,
                    1 - (embedding <=> $1::vector) as similarity_score
                FROM book_chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            """
            
            rows = await db.fetch(search_query, query_embedding, top_k)
            
            chunks = []
            for row in rows:
                chunks.append({
                    "id": row["id"],
                    "chunk_text": row["chunk_text"],
                    "chapter_title": row["chapter_title"],
                    "section_title": row["section_title"],
                    "page_number": row["page_number"],
                    "chunk_index": row["chunk_index"],
                    "similarity_score": float(row["similarity_score"])
                })
            
            logger.info(f"Retrieved {len(chunks)} chunks. Top similarity: {chunks[0]['similarity_score']:.3f if chunks else 0}")
            return chunks
        
        except Exception as e:
            logger.error(f"Error retrieving chunks: {e}", exc_info=True)
            return []
    
    async def answer_question(self, question: str) -> Dict[str, Any]:
        """
        Answer a question based on the book content (Mode A: Strict book-only)
        
        Returns:
            Dict with answer, response_type, chunks_used, confidence_score, tokens_used
        """
        try:
            # Retrieve relevant chunks
            chunks = await self.retrieve_relevant_chunks(question)
            
            if not chunks:
                logger.warning("No chunks found in database")
                return {
                    "answer": "",
                    "response_type": "refused",
                    "chunks_used": [],
                    "confidence_score": 0.0,
                    "tokens_used": 0
                }
            
            # Check if top chunk meets similarity threshold
            top_similarity = chunks[0]["similarity_score"]
            
            if top_similarity < settings.SIMILARITY_THRESHOLD:
                logger.info(f"Top similarity {top_similarity:.3f} below threshold {settings.SIMILARITY_THRESHOLD}")
                return {
                    "answer": "",
                    "response_type": "refused",
                    "chunks_used": [c["id"] for c in chunks],
                    "confidence_score": top_similarity,
                    "tokens_used": 0
                }
            
            # Build context from chunks
            context = self._build_context(chunks)
            
            # Generate answer using LLM
            answer, tokens_used = await self._generate_answer(question, context, chunks)
            
            # Verify answer is grounded (basic check)
            if self._is_answer_grounded(answer):
                return {
                    "answer": answer,
                    "response_type": "answered",
                    "chunks_used": [c["id"] for c in chunks],
                    "confidence_score": top_similarity,
                    "tokens_used": tokens_used
                }
            else:
                logger.warning("Generated answer not grounded in book content")
                return {
                    "answer": "",
                    "response_type": "refused",
                    "chunks_used": [c["id"] for c in chunks],
                    "confidence_score": top_similarity,
                    "tokens_used": tokens_used
                }
        
        except Exception as e:
            logger.error(f"Error answering question: {e}", exc_info=True)
            return {
                "answer": "",
                "response_type": "error",
                "chunks_used": [],
                "confidence_score": 0.0,
                "tokens_used": 0
            }
    
    def _build_context(self, chunks: List[Dict[str, Any]]) -> str:
        """Build context string from chunks"""
        context_parts = []
        
        for i, chunk in enumerate(chunks, 1):
            citation = self._format_citation(chunk)
            context_parts.append(f"[{i}] {citation}\n{chunk['chunk_text']}\n")
        
        return "\n".join(context_parts)
    
    def _format_citation(self, chunk: Dict[str, Any]) -> str:
        """Format citation for a chunk"""
        parts = []
        
        if chunk.get("chapter_title"):
            parts.append(f"Chapter: {chunk['chapter_title']}")
        
        if chunk.get("section_title"):
            parts.append(f"Section: {chunk['section_title']}")
        
        if chunk.get("page_number"):
            parts.append(f"Page {chunk['page_number']}")
        
        return " | ".join(parts) if parts else "Source: Book"
    
    async def _generate_answer(
        self,
        question: str,
        context: str,
        chunks: List[Dict[str, Any]]
    ) -> tuple[str, int]:
        """Generate answer using LLM with strict grounding"""
        
        system_prompt = f"""You are an AI assistant that answers questions ONLY based on the book '{settings.BOOK_TITLE}'.

CRITICAL RULES:
1. Answer ONLY using information explicitly stated in the provided context
2. NEVER add information from your general knowledge
3. If the context doesn't contain the answer, say "I cannot answer this based on the book"
4. Always cite your sources using [1], [2], etc. matching the context numbers
5. Keep answers concise and accurate

Your goal is to help readers understand the book content, not to provide general knowledge."""

        user_prompt = f"""Question: {question}

Context from the book:
{context}

Instructions:
- Answer the question using ONLY the information from the context above
- Include citations [1], [2], etc. for each claim
- Format your answer clearly with citations at the end
- If the context doesn't fully answer the question, say so clearly

Answer:"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=settings.MAX_TOKENS_RESPONSE,
                temperature=0.3  # Low temperature for more factual responses
            )
            
            answer = response.choices[0].message.content.strip()
            tokens_used = response.usage.total_tokens
            
            # Add formatted citations at the end
            citations_text = self._format_citations_list(chunks)
            if citations_text:
                answer += f"\n\n📚 Sources:\n{citations_text}"
            
            return answer, tokens_used
        
        except Exception as e:
            logger.error(f"Error generating answer with LLM: {e}", exc_info=True)
            raise
    
    def _format_citations_list(self, chunks: List[Dict[str, Any]]) -> str:
        """Format list of citations"""
        citations = []
        for i, chunk in enumerate(chunks, 1):
            citation = self._format_citation(chunk)
            citations.append(f"[{i}] {citation}")
        
        return "\n".join(citations)
    
    def _is_answer_grounded(self, answer: str) -> bool:
        """
        Basic check if answer is grounded (not a refusal)
        
        More sophisticated grounding verification could be added here
        """
        refusal_phrases = [
            "cannot answer",
            "don't have information",
            "not mentioned in the book",
            "not covered in the book",
            "context doesn't contain"
        ]
        
        answer_lower = answer.lower()
        return not any(phrase in answer_lower for phrase in refusal_phrases)