export interface ChatRequest {
  message: string;
  history: ChatHistoryMessage[];
  referenceHistory: ChatHistoryMessage[];
}

export interface ChatResponse {
  response: string;
  session_id: number;
  attachment?: ChatDocumentAttachment;
  analysis_status?: 'complete' | 'unavailable' | 'skipped';
}

export interface ChatDocumentAttachment {
  id: number;
  filename: string;
  content_type: string;
  file_size: number;
  kind: 'uploaded' | 'generated';
}

export interface PendingChatResponse {
  conversationId: string;
  request: ChatRequest;
}

export interface ChatMessage {
  id?: number;
  sender: 'user' | 'bot';
  text: string;
  createdAt?: string;
  imageUrl?: string;
  attachment?: ChatDocumentAttachment;
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
