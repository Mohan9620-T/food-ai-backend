import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject } from 'rxjs';
import { ChatMessage } from '../models/chat';

@Injectable({
  providedIn: 'root'
})
export class ChatService {

  private apiUrl = 'http://127.0.0.1:8000/chat/';

  constructor(private http: HttpClient) {}

  private messagesSubject = new BehaviorSubject<ChatMessage[]>([]);

  messages$ = this.messagesSubject.asObservable();

  addMessage(msg: ChatMessage) {
    this.messagesSubject.next([
      ...this.messagesSubject.value,
      msg
    ]);
  }

  sendMessage(data: { message: string }) {
    return this.http.post<any>(this.apiUrl, data);
  }

}