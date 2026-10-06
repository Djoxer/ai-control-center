import { Component, CUSTOM_ELEMENTS_SCHEMA, inject } from '@angular/core';
import { SidebarService } from './sidebar.service';
import { Icon } from './icon';
import { ShellStore } from '../core/shell.store';

@Component({
  selector: 'app-topbar',
  imports: [Icon],
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  templateUrl: './topbar.html'
})
export class Topbar {
  readonly sidebar = inject(SidebarService);
  readonly shell = inject(ShellStore);   // backend status pill
}
