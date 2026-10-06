import { Component, OnInit, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Sidebar } from './layout/sidebar';
import { Topbar } from './layout/topbar';
import { ShellStore } from './core/shell.store';

@Component({
  selector: 'app-root',
  imports: [Sidebar, Topbar, RouterOutlet],
  templateUrl: './app.html'
})
export class App implements OnInit {
  private readonly shell = inject(ShellStore);

  ngOnInit(): void {
    this.shell.start();   // health check + live stream; feeds topbar status and sidebar module states
  }
}
