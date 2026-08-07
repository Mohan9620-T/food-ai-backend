export interface ChatRequest {
  message: string;
  history: ChatHistoryMessage[];
  referenceHistory: ChatHistoryMessage[];
}

export interface ChatResponse {
  response: string;
}

export interface ChatMessage {
  sender: 'user' | 'bot';
  text: string;
}

export interface ChatHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatConversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
}
