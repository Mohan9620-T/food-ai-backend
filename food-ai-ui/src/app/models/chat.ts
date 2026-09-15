export interface ChatRequest {
  message: string;
  history: ChatHistoryMessage[];
  referenceHistory: ChatHistoryMessage[];
}

export interface ChatResponse {
  response: string;
  session_id: number;
  attachment?: ChatDocumentAttachment;
  attachments?: ChatDocumentAttachment[];
  analysis_status?: 'complete' | 'unavailable' | 'skipped';
  status?: 'done' | 'clarification_required' | 'ready_for_review' | 'partial' | 'failed';
  plan_summary?: string | null;
  clarification?: ClarificationQuestion | null;
  latest_document_id?: number | null;
  steps?: ChatDocumentAutomationStep[];
  fidelity?: 'FULL' | 'HIGH' | 'PARTIAL' | 'BEST_EFFORT' | 'UNSUPPORTED' | null;
  fidelity_note?: string | null;
}

export interface ClarificationQuestion {
  question: string;
  options: { id: string; label: string; description?: string | null; recommended: boolean }[];
  allow_other: boolean;
}

export interface DocumentAutomationTurn {
  response: ChatResponse;
  instruction: string;
  sourceDocumentId?: number;
  choices: { question: string; answer: string }[];
}

export interface ChatDocumentAutomationStep {
  position: number;
  operation: string;
  source_document_ids: number[];
  output_type: string;
  parameters: Record<string, unknown>;
  output_document_id?: number | null;
  filename?: string | null;
  status: 'completed' | 'failed' | 'not_started';
  detail?: string | null;
  fidelity?: 'FULL' | 'HIGH' | 'PARTIAL' | 'BEST_EFFORT' | 'UNSUPPORTED' | null;
  fidelity_note?: string | null;
}

export interface ChatDocumentAttachment {
  id: number;
  filename: string;
  content_type: string;
  file_size: number;
  kind: 'uploaded' | 'generated';
  provenance?: 'uploaded_source' | 'general_knowledge' | null;
  source_document_ids?: number[];
  assumptions?: string[];
}

export interface PendingChatResponse {
  conversationId: string;
  request: ChatRequest;
}

export interface ChatMessage {
  automation?: DocumentAutomationTurn;
  id?: number;
  sender: 'user' | 'bot';
  text: string;
  createdAt?: string;
  imageUrl?: string;
  attachment?: ChatDocumentAttachment;
  attachments?: ChatDocumentAttachment[];
}

export interface ChatHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatConversation {
  id: string;
  sessionId?: number;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
}
