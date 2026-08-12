import { RenderMode, ServerRoute } from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
  {
    // Authentication is stored in localStorage, which is only available in
    // the browser. Rendering this route on the server would make the auth
    // guard treat every page refresh as a logged-out request.
    path: '',
    renderMode: RenderMode.Client
  },
  {
    path: '**',
    renderMode: RenderMode.Prerender
  }
];
