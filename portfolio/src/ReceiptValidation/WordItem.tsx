import React from 'react';
import { ReceiptWord, ReceiptWordTag } from '../interfaces';
import TagMenu from './TagMenu';

interface WordItemProps {
  word: ReceiptWord;
  tag: ReceiptWordTag;
  isSelected: boolean;
  onWordClick: () => void;
  onTagClick: () => void;
  openTagMenu: boolean;
  menuRef: React.RefObject<HTMLDivElement>;
}

const WordItem: React.FC<WordItemProps> = ({
  word,
  tag,
  isSelected,
  onWordClick,
  onTagClick,
  openTagMenu,
  menuRef,
}) => {
  const renderStars = (confidence: number | null) => {
    if (confidence === null) return null;
    const stars = '★'.repeat(confidence) + '☆'.repeat(5 - confidence);
    return (
      <span style={{ 
        color: 'var(--text-color)',
        marginLeft: '8px',
        fontSize: '0.875rem'
      }}>
        {stars}
      </span>
    );
  };

  const renderHumanValidation = (validated: boolean | null) => {
    if (validated === null) {
      return (
        <span style={{ 
          width: '16px',
          height: '16px',
          borderRadius: '4px',
          border: '2px solid var(--text-color)',
          display: 'inline-block'
        }} />
      );
    }
    return (
      <span style={{ 
        color: validated ? '#4ade80' : '#ef4444',
        fontSize: '1rem'
      }}>
        {validated ? '✓' : '✗'}
      </span>
    );
  };

  return (
    <div
      style={{
        cursor: 'pointer',
        padding: '4px',
        backgroundColor: isSelected ? '#1e40af' : 'transparent',
        borderRadius: '2px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        position: 'relative'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center' }}>
        {renderHumanValidation(tag.human_validated)}
      </div>

      <div 
        style={{ 
          flex: 1,
          color: 'var(--text-color)',
          padding: '0 8px',
          cursor: 'pointer'
        }}
        onClick={(e) => {
          e.stopPropagation();
          onWordClick();
        }}
      >
        {word.text}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        {renderStars(tag.gpt_confidence)}
        <span 
          onClick={onTagClick}
          style={{ 
            color: tag.validated ? '#4ade80' : '#ef4444',
            backgroundColor: tag.validated ? '#065f46' : '#7f1d1d',
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '0.875rem',
            cursor: 'pointer'
          }}
        >
          {tag.tag}
        </span>
        {openTagMenu && (
          <TagMenu
            menuRef={menuRef}
            onSelect={(newTag) => {
              console.log('Selected new tag:', newTag);
            }}
          />
        )}
      </div>
    </div>
  );
};

export default WordItem; 