import React from 'react';

interface TagMenuProps {
  menuRef: React.RefObject<HTMLDivElement>;
  onSelect: (tag: string) => void;
  style?: React.CSSProperties;
}

const selectableTags = [
  "line_item_name",
  "address",
  "line_item_price",
  "store_name",
  "date",
  "time",
  "total_amount",
  "phone_number",
  "taxes",
];

const TagMenu: React.FC<TagMenuProps> = ({ menuRef, onSelect, style }) => {
  return (
    <div 
      ref={menuRef}
      style={{
        position: 'absolute',
        top: '100%',
        backgroundColor: 'var(--background-color)',
        border: '1px solid var(--text-color)',
        borderRadius: '4px',
        zIndex: 10,
        marginTop: '4px',
        boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
        ...style
      }}
    >
      {selectableTags.map(tag => (
        <div
          key={tag}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(tag);
          }}
          className="hover:bg-gray-700"
          style={{
            padding: '8px 16px',
            color: 'var(--text-color)',
            cursor: 'pointer',
            whiteSpace: 'nowrap'
          }}
        >
          {tag.split('_').map(word => 
              word.charAt(0).toUpperCase() + word.slice(1)
            ).join(' ')}
        </div>
      ))}
    </div>
  );
};

export default TagMenu; 