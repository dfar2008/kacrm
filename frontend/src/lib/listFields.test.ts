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

test('fractional monetary amounts override rounded display siblings', () => {
  for (const name of ['amount', 'final_amount', 'dueamount', 'payamount', 'actualamount', 'budget']) {
    const field = { field_name: name, field_type: name === 'budget' ? 'currency' : 'number' } as CrmFieldMeta;
    expect(formatListFieldValue(t, field, { [name]: '25.50', [name + '_display']: '26' } as CrmRecord)).toBe('¥25.5');
  }
});

test('cashflow lists identify the actual receipt or payment, not its related ID', async () => {
  const { defaultListFields, listPrimaryText, resolveListFields } = await import('./listFields');
  for (const [module, relation] of [['receivables', 'accounts_id'], ['payments', 'receivables_id']]) {
    const name = `${module}_name`;
    const record = { id: 16, [name]: '本轮测试记录', [relation]: 34 } as CrmRecord;
    const fields = Object.fromEntries([
      { field_name: name, field_type: 'text' },
      { field_name: relation, field_type: 'relation' },
    ].map(f => [f.field_name, f as CrmFieldMeta]));
    expect(listPrimaryText(t, defaultListFields(module).row1, fields, record)).toBe('本轮测试记录');
    expect(listPrimaryText(t, [], {}, record)).toBe('本轮测试记录');
    const custom = { row1: [relation], row2: [] };
    expect(resolveListFields(module, custom, null)).toEqual(custom);
  }
});
