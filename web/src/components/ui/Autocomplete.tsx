import { useMemo, type InputHTMLAttributes } from 'react';
import { Autocomplete as Primitive } from '@base-ui/react/autocomplete';
import { ChevronDown } from 'lucide-react';
import { Input } from './Input';
import { Button } from './Button';
import { cn } from '@/lib/cn';
import { useI18n } from '@/i18n/locale';

export type AutocompleteOption = { value: string; label: string; group?: string };

type AutocompleteGroup = { label: string; items: AutocompleteOption[] };

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'defaultValue' | 'onChange' | 'list' | 'size'> & {
  value: string;
  options: string[] | AutocompleteOption[];
  onValueChange(value: string): void;
};

// Normalize the two accepted option shapes to one object shape so the list
// renderer and group builder below can treat them uniformly.
function toOptions(options: string[] | AutocompleteOption[]): AutocompleteOption[] {
  return options.map((option) => (typeof option === 'string' ? { value: option, label: option } : option));
}

// Group options by their `group` field, preserving first-seen order. Returns
// null when no option carries a group (the flat path), which keeps the list
// output byte-identical to the pre-grouping behaviour for existing callers.
function buildGroups(options: AutocompleteOption[]): AutocompleteGroup[] | null {
  if (!options.some((option) => option.group != null)) return null;
  const groups: AutocompleteGroup[] = [];
  const byLabel = new Map<string, AutocompleteOption[]>();
  for (const option of options) {
    const label = option.group ?? '';
    let items = byLabel.get(label);
    if (!items) {
      items = [];
      byLabel.set(label, items);
      groups.push({ label, items });
    }
    items.push(option);
  }
  return groups;
}

// Free text with suggestions, using the same controls and popup styles as Select.
// Options may be plain strings or `{value,label,group}` objects; grouped options
// render a small header before the first item of each group.
export function Autocomplete({ value, options, onValueChange, className, disabled, ...props }: Props) {
  const { tr } = useI18n();
  const flat = useMemo(() => toOptions(options), [options]);
  const groups = useMemo(() => buildGroups(flat), [flat]);
  // base-ui's AutocompleteRoot is overloaded for flat vs. grouped items and
  // can't unify the union; it accepts both shapes at runtime, so cast here.
  const items = (groups ?? flat) as AutocompleteOption[];

  return <Primitive.Root items={items} value={value} onValueChange={onValueChange} disabled={disabled} openOnInputClick>
    <Primitive.InputGroup className={cn('relative w-full', className)}>
      <Primitive.Input render={<Input />} {...props} className="pr-9" />
      <Primitive.Trigger render={<Button variant="subtle" size="icon" />} className="absolute right-0 top-0" aria-label={tr(`展开${props['aria-label'] ?? ''}选项`, `Show ${props['aria-label'] ?? ''} options`)}>
        <ChevronDown size={14} aria-hidden="true" className="text-text-faint" />
      </Primitive.Trigger>
    </Primitive.InputGroup>
    <Primitive.Portal>
      <Primitive.Positioner sideOffset={4} align="start" className="og-select-positioner">
        <Primitive.Popup className="og-select-popup">
          <Primitive.Empty><div className="px-3 py-4 text-xs text-text-muted">{tr('没有匹配项，可直接输入新值。', 'No matches. You can type a new value.')}</div></Primitive.Empty>
          {groups ? (
            <Primitive.List className="og-select-list">
              {(group: AutocompleteGroup) => (
                <Primitive.Group key={group.label} items={group.items}>
                  {group.label && <Primitive.GroupLabel className="og-select-group-label">{group.label}</Primitive.GroupLabel>}
                  <Primitive.Collection>
                    {(option: AutocompleteOption) => (
                      <Primitive.Item key={option.value} value={option} className="og-select-item"><span className="min-w-0 whitespace-normal break-words">{option.label}</span></Primitive.Item>
                    )}
                  </Primitive.Collection>
                </Primitive.Group>
              )}
            </Primitive.List>
          ) : (
            <Primitive.List className="og-select-list">
              {(option: AutocompleteOption) => <Primitive.Item key={option.value} value={option} className="og-select-item"><span className="min-w-0 whitespace-normal break-words">{option.label}</span></Primitive.Item>}
            </Primitive.List>
          )}
        </Primitive.Popup>
      </Primitive.Positioner>
    </Primitive.Portal>
  </Primitive.Root>;
}
