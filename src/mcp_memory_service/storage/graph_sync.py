"""
Phase 9H: Kuzu Graph Sync for Memory Provenance

Syncs memories to Kuzu graph and detects provenance relationships:
- RELATES_TO: Similar content (semantic similarity > threshold)
- SUPERSEDES: Newer memory on same topic replaces older
- CONTRADICTS: Conflicting information detected
"""

import kuzu
import os
import re
import logging
from typing import Optional, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Graph database path
GRAPH_DB_PATH = os.path.expanduser("~/.local/share/mcp-memory/memory_graph")

# Thresholds for provenance detection
RELATES_TO_THRESHOLD = 0.75  # Similarity score for RELATES_TO
SUPERSEDES_THRESHOLD = 0.90  # Very high similarity = likely supersedes
MAX_RELATIONS_PER_MEMORY = 5  # Don't create too many edges


class GraphSync:
    """Handles syncing memories to Kuzu graph with provenance detection.

    Supports context manager pattern to automatically release DB lock:
        with GraphSync() as gs:
            gs.sync_with_provenance(...)
        # Lock released automatically
    """

    def __init__(self, db_path: str = GRAPH_DB_PATH):
        self.db_path = db_path
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None

    def __enter__(self):
        """Context manager entry - connection is lazy, so just return self."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release the database lock."""
        self.close()
        return False  # Don't suppress exceptions

    @property
    def conn(self) -> kuzu.Connection:
        """Lazy connection to Kuzu database."""
        if self._conn is None:
            self._db = kuzu.Database(self.db_path)
            self._conn = kuzu.Connection(self._db)
        return self._conn

    def close(self):
        """Close database connection and release lock."""
        if self._conn:
            self._conn = None
        if self._db:
            self._db = None
    
    def _sanitize_string(self, s: str, max_len: int = 200) -> str:
        """Sanitize string for Cypher query."""
        if not s:
            return ""
        # Truncate and remove problematic chars
        s = s[:max_len]
        s = re.sub(r'[\'\"\\`]', '', s)
        s = re.sub(r'[\x00-\x1f]', ' ', s)
        return s
    
    def node_exists(self, content_hash: str) -> bool:
        """Check if memory node already exists."""
        try:
            result = self.conn.execute(
                f"MATCH (m:Memory {{hash: '{content_hash}'}}) RETURN m.hash"
            )
            return result.has_next()
        except Exception as e:
            logger.error(f"Error checking node existence: {e}")
            return False
    
    def add_memory_node(
        self,
        content_hash: str,
        content: str,
        memory_type: Optional[str] = None,
        created_at: Optional[str] = None
    ) -> bool:
        """Add a memory node to the graph."""
        if self.node_exists(content_hash):
            logger.debug(f"Node {content_hash[:16]}... already exists")
            return False
        
        try:
            preview = self._sanitize_string(content, 200)
            mem_type = self._sanitize_string(memory_type or "unknown", 50)
            created = created_at or datetime.now().isoformat()
            
            self.conn.execute(f"""
                CREATE (m:Memory {{
                    hash: '{content_hash}',
                    content_preview: '{preview}',
                    created_at: '{created}',
                    memory_type: '{mem_type}'
                }})
            """)
            logger.info(f"Added memory node: {content_hash[:16]}...")
            return True
        except Exception as e:
            logger.error(f"Error adding memory node: {e}")
            return False
    
    def add_relates_to(
        self,
        from_hash: str,
        to_hash: str,
        strength: float,
        created_at: Optional[str] = None
    ) -> bool:
        """Create RELATES_TO edge between memories."""
        try:
            created = created_at or datetime.now().isoformat()
            self.conn.execute(f"""
                MATCH (a:Memory {{hash: '{from_hash}'}}), (b:Memory {{hash: '{to_hash}'}})
                CREATE (a)-[:RELATES_TO {{strength: {strength}, created_at: '{created}'}}]->(b)
            """)
            logger.debug(f"Created RELATES_TO: {from_hash[:8]}... -> {to_hash[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Error creating RELATES_TO edge: {e}")
            return False
    
    def add_supersedes(
        self,
        new_hash: str,
        old_hash: str,
        reason: str = "Updated information",
        created_at: Optional[str] = None
    ) -> bool:
        """Create SUPERSEDES edge (new memory replaces old)."""
        try:
            created = created_at or datetime.now().isoformat()
            reason_safe = self._sanitize_string(reason, 100)
            self.conn.execute(f"""
                MATCH (new:Memory {{hash: '{new_hash}'}}), (old:Memory {{hash: '{old_hash}'}})
                CREATE (new)-[:SUPERSEDES {{reason: '{reason_safe}', created_at: '{created}'}}]->(old)
            """)
            logger.info(f"Created SUPERSEDES: {new_hash[:8]}... -> {old_hash[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Error creating SUPERSEDES edge: {e}")
            return False
    
    def add_contradicts(
        self,
        hash_a: str,
        hash_b: str,
        resolution: str = "unresolved",
        detected_at: Optional[str] = None
    ) -> bool:
        """Create CONTRADICTS edge between conflicting memories."""
        try:
            detected = detected_at or datetime.now().isoformat()
            resolution_safe = self._sanitize_string(resolution, 100)
            self.conn.execute(f"""
                MATCH (a:Memory {{hash: '{hash_a}'}}), (b:Memory {{hash: '{hash_b}'}})
                CREATE (a)-[:CONTRADICTS {{detected_at: '{detected}', resolution: '{resolution_safe}'}}]->(b)
            """)
            logger.info(f"Created CONTRADICTS: {hash_a[:8]}... <-> {hash_b[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Error creating CONTRADICTS edge: {e}")
            return False
    
    def ensure_node_exists(self, content_hash: str) -> bool:
        """Ensure a node exists in the graph (create stub if missing)."""
        if self.node_exists(content_hash):
            return True
        try:
            # Create a stub node - will be updated if memory is stored later
            self.conn.execute(f"""
                CREATE (m:Memory {{
                    hash: '{content_hash}',
                    content_preview: '(stub - created for edge)',
                    created_at: '{datetime.now().isoformat()}',
                    memory_type: 'stub'
                }})
            """)
            logger.debug(f"Created stub node for {content_hash[:16]}...")
            return True
        except Exception as e:
            logger.error(f"Error creating stub node: {e}")
            return False

    def sync_with_provenance(
        self,
        content_hash: str,
        content: str,
        memory_type: Optional[str],
        created_at: Optional[str],
        similar_memories: List[Tuple[str, float]]  # [(hash, similarity_score), ...]
    ) -> dict:
        """
        Sync a new memory to graph and create provenance edges.

        Args:
            content_hash: Hash of the new memory
            content: Full content text
            memory_type: Type of memory
            created_at: ISO timestamp
            similar_memories: List of (hash, similarity) from vector search

        Returns:
            dict with sync results
        """
        result = {
            "node_created": False,
            "relates_to": [],
            "supersedes": [],
            "contradicts": []
        }

        # Add the node
        result["node_created"] = self.add_memory_node(
            content_hash, content, memory_type, created_at
        )

        if not result["node_created"]:
            return result

        # Process similar memories for provenance
        for related_hash, similarity in similar_memories[:MAX_RELATIONS_PER_MEMORY]:
            if related_hash == content_hash:
                continue

            # Ensure target node exists (create stub if not)
            self.ensure_node_exists(related_hash)

            if similarity >= SUPERSEDES_THRESHOLD:
                # Very high similarity - this likely supersedes the old one
                if self.add_supersedes(content_hash, related_hash, "High similarity update"):
                    result["supersedes"].append(related_hash)
            elif similarity >= RELATES_TO_THRESHOLD:
                # Moderate similarity - related content
                if self.add_relates_to(content_hash, related_hash, similarity):
                    result["relates_to"].append(related_hash)
        
        logger.info(
            f"Synced {content_hash[:16]}... - "
            f"relates_to: {len(result['relates_to'])}, "
            f"supersedes: {len(result['supersedes'])}"
        )
        
        return result
    
    def get_provenance_chain(self, content_hash: str, depth: int = 3) -> List[dict]:
        """Get the provenance chain for a memory (what it supersedes/relates to)."""
        try:
            result = self.conn.execute(f"""
                MATCH path = (m:Memory {{hash: '{content_hash}'}})-[r:SUPERSEDES|RELATES_TO*1..{depth}]->(related:Memory)
                RETURN related.hash, related.content_preview, type(r), length(path)
                ORDER BY length(path)
            """)
            
            chain = []
            while result.has_next():
                row = result.get_next()
                chain.append({
                    "hash": row[0],
                    "preview": row[1],
                    "relationship": row[2],
                    "depth": row[3]
                })
            return chain
        except Exception as e:
            logger.error(f"Error getting provenance chain: {e}")
            return []
    
    def get_stats(self) -> dict:
        """Get graph statistics."""
        try:
            stats = {}
            
            # Node count
            result = self.conn.execute("MATCH (m:Memory) RETURN count(m)")
            stats["nodes"] = result.get_next()[0]
            
            # Edge counts by type
            for edge_type in ["RELATES_TO", "SUPERSEDES", "CONTRADICTS", "CAUSED_BY", "DERIVED_FROM"]:
                result = self.conn.execute(f"MATCH ()-[r:{edge_type}]->() RETURN count(r)")
                stats[f"edges_{edge_type.lower()}"] = result.get_next()[0]
            
            stats["total_edges"] = sum(v for k, v in stats.items() if k.startswith("edges_"))
            
            return stats
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}


# Singleton instance
_graph_sync: Optional[GraphSync] = None

def get_graph_sync() -> GraphSync:
    """Get or create the GraphSync singleton."""
    global _graph_sync
    if _graph_sync is None:
        _graph_sync = GraphSync()
    return _graph_sync
