export interface ChatRequest {
  message: string;
  history: ChatHistoryMessage[];
  referenceHistory: ChatHistoryMessage[];
}

export interface ChatResponse {
  response: string;
  session_id: number;
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
