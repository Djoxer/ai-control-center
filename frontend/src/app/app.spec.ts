import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { sidebarServiceStub } from './testing/sidebar-service.stub';
import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting(), sidebarServiceStub],
    }).compileComponents();
  });

  it('creates the shell and asks the backend for health once', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();                                          // triggers ngOnInit
    TestBed.inject(HttpTestingController).expectOne('/api/v1/health'); // exactly one call
    expect(fixture.componentInstance).toBeTruthy();
  });
});
