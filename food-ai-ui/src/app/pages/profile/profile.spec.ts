import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';

import { ProfilePage } from './profile';
import { ProfileService, UserProfile } from '../../services/profile';

@Component({ selector: 'app-sidebar', template: '' })
class SidebarStub {}

const loadedProfile: UserProfile = {
  id: 1,
  user_id: 7,
  updated_at: '2026-09-02T00:00:00Z',
  goal: 'maintain',
  target_calories: 2000,
  target_protein_g: null,
  target_carbs_g: null,
  target_fat_g: null,
  allergies: ['peanuts'],
  dietary_restrictions: [],
  disliked_foods: []
};

class ProfileServiceStub {
  readonly loading = signal(false);
  readonly load = vi.fn(() => of(loadedProfile));
  readonly save = vi.fn(() => of(loadedProfile));
}

describe('ProfilePage', () => {
  let fixture: ComponentFixture<ProfilePage>;
  let service: ProfileServiceStub;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [ProfilePage] })
      .overrideComponent(ProfilePage, { set: { imports: [FormsModule, SidebarStub] } })
      .overrideProvider(ProfileService, { useFactory: () => new ProfileServiceStub() })
      .compileComponents();
    fixture = TestBed.createComponent(ProfilePage);
    service = TestBed.inject(ProfileService) as unknown as ProfileServiceStub;
    fixture.detectChanges();
  });

  it('renders the loaded nutrition profile', () => {
    expect(fixture.nativeElement.querySelector('h1')?.textContent).toContain('Nutrition Profile');
    expect(fixture.componentInstance.allergies).toBe('peanuts');
  });

  it('normalizes comma-separated preferences before saving', () => {
    fixture.componentInstance.allergies = ' peanuts, shellfish, peanuts ';
    fixture.componentInstance.save();
    expect(service.save).toHaveBeenCalledWith(expect.objectContaining({ allergies: ['peanuts', 'shellfish'] }));
    expect(fixture.componentInstance.message).toBe('Profile saved.');
  });

  it('shows an error when saving fails', () => {
    service.save.mockReturnValue(throwError(() => new Error('offline')));
    fixture.componentInstance.save();
    expect(fixture.componentInstance.error).toBe('Profile could not be saved.');
  });
});
