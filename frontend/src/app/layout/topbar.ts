import { Component, CUSTOM_ELEMENTS_SCHEMA, inject } from '@angular/core';
import { SidebarService } from '../layout/sidebar.service';
import { Icon } from './icon';

@Component({
  selector: 'app-topbar',
  imports: [Icon],
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  templateUrl: './topbar.html'
})
export class Topbar {
  readonly sidebar = inject(SidebarService);
}
