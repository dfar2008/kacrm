import { describe, expect, test } from 'bun:test';
import { createRecordValues, createValueErrors } from './createRecordValues';
import type { CrmFieldMeta } from './api';
const fields = [
  { field_name: 'price', field_type: 'currency' },
  { field_name: 'stock', field_type: 'number' },
  { field_name: 'mobile', field_type: 'phone' },
] as CrmFieldMeta[];
describe('create record JSON values', () => {
  test('sends decimal prices and integer counts as JSON numbers', () => {
    expect(createRecordValues(fields, { price: '12.5', stock: '10' })).toEqual({ price: 12.5, stock: 10 });
  });
  test('preserves text and meaningful zero while omitting unset fields', () => {
    expect(createRecordValues(fields, { mobile: '00123', price: '0', stock: '', other: null })).toEqual({ mobile: '00123', price: 0 });
  });
  test('keeps invalid numeric input for validation instead of silently sending zero or null', () => {
    expect(createRecordValues(fields, { price: 'bad', stock: 'Infinity' })).toEqual({ price: 'bad', stock: 'Infinity' });
  });
  test('does not silently round fractional values in legacy number fields', () => {
    expect(createRecordValues(fields, { stock: '12.5' })).toEqual({ stock: '12.5' });
  });
  test('permits fractional prices in decimal fields', () => {
    expect(createValueErrors(fields, { price: '12.5' })).toEqual({});
  });
  test('rejects invalid numeric input before a create request', () => {
    expect(createValueErrors(fields, { price: 'bad', stock: 'Infinity' })).toEqual({ price: 'number', stock: 'number' });
  });
});
