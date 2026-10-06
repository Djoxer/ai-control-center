import { Component, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Sidebar } from './layout/sidebar';
import { Topbar } from './layout/topbar';

@Component({
  selector: 'app-root',
  imports: [Sidebar, Topbar, RouterOutlet],
  templateUrl: './app.html'
})
export class App {
  protected readonly title = signal('ai-control-center');
}
