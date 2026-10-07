import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { TitleStrategy, provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { AppTitleStrategy } from './core/title.strategy';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    { provide: TitleStrategy, useClass: AppTitleStrategy },   // tab title: 'Seite · AI Control Center'
    provideHttpClient(withFetch()),   // generated API client needs HttpClient; URLs stay relative (/api/...)
  ]
};
