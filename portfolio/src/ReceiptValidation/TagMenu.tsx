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
  React.useEffect(() => {
    if (!menuRef.current) return;
    
    const menu = menuRef.current;
    const menuRect = menu.getBoundingClientRect();
    
    // Handle explicit positioning (SelectedReceipt case)
    if (style?.left !== undefined || style?.top !== undefined) {
      const viewportHeight = window.innerHeight;
      const viewportWidth = window.innerWidth;
      
      if (menuRect.bottom > viewportHeight) {
        menu.style.transform = `translateY(-100%)`;
      }
      
      if (menuRect.right > viewportWidth) {
        menu.style.transform = `translateX(-100%)${menu.style.transform ? ' translateY(-100%)' : ''}`;
      }
    } 
    // Handle relative positioning (WordItem case)
    else {
      const parentRect = menu.parentElement?.getBoundingClientRect();
      if (!parentRect) return;

      const viewportHeight = window.innerHeight;
      const viewportWidth = window.innerWidth;
      
      const spaceBelow = viewportHeight - parentRect.bottom;
      const spaceRight = viewportWidth - parentRect.right;
      
      if (spaceBelow < menuRect.height) {
        menu.style.top = 'auto';
        menu.style.bottom = '100%';
        menu.style.marginBottom = '4px';
      } else {
        menu.style.top = '100%';
        menu.style.bottom = 'auto';
        menu.style.marginTop = '4px';
      }
      
      if (spaceRight < menuRect.width) {
        menu.style.right = '0';
        menu.style.left = 'auto';
      } else {
        menu.style.left = '0';
        menu.style.right = 'auto';
      }
    }
  }, [style, menuRef]);

  return (
    <div
      ref={menuRef}
      style={{
        position: 'absolute',
        backgroundColor: 'var(--background-color)',
        border: '1px solid var(--text-color)',
        borderRadius: '4px',
        zIndex: 1000,
        boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
        minWidth: 'max-content',
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