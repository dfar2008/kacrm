import { expect, test } from 'bun:test';
import { formatListFieldValue } from './listFields';
import type { CrmFieldMeta, CrmRecord } from './api';
import type { T } from './i18n';

const t = ((key: string) => key) as T;
test('product prices retain cents despite rounded backend display siblings', () => {
  const field = { field_name: 'price', field_type: 'currency' } as CrmFieldMeta;
  expect(formatListFieldValue(t, field, { price: '12.75', price_display: '13' } as CrmRecord)).toBe('¥12.75');
  expect(formatListFieldValue(t, field, { price: '12345.67' } as CrmRecord)).toBe('¥12,345.67');
  expect(formatListFieldValue(t, field, { price: 0 } as CrmRecord)).toBe('¥0');
});
