import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Title } from '@angular/platform-browser';
import { Router, TitleStrategy, provideRouter } from '@angular/router';

import { APP_NAME, AppTitleStrategy } from './title.strategy';

@Component({ template: '' })
class Page {}

describe('AppTitleStrategy', () => {
  it('puts the page in front of the app name, and falls back to the app name', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: 'logs', component: Page, title: 'Protokoll' }, { path: 'x', component: Page }]),
        { provide: TitleStrategy, useClass: AppTitleStrategy },
      ],
    });
    const router = TestBed.inject(Router);
    const title = TestBed.inject(Title);
    await router.navigateByUrl('/logs');
    expect(title.getTitle()).toBe(`Protokoll · ${APP_NAME}`);
    await router.navigateByUrl('/x');
    expect(title.getTitle()).toBe(APP_NAME);
  });
});
