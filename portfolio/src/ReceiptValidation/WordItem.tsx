import React, { useState } from 'react';
import { ReceiptWord, ReceiptWordTag } from '../interfaces';
import TagMenu from './TagMenu';
import { postReceiptWordTag } from '../api';

interface WordItemProps {
  word: ReceiptWord;
  tag: ReceiptWordTag;
  isSelected: boolean;
  onWordClick: () => void;
  onTagClick: () => void;
  openTagMenu: boolean;
  menuRef: React.RefObject<HTMLDivElement>;
  onUpdateTag?: (updatedTag: ReceiptWordTag) => void;
}

const WordItem: React.FC<WordItemProps> = ({
  word,
  tag,
  isSelected,
  onWordClick,
  onTagClick,
  openTagMenu,
  menuRef,
  onUpdateTag,
}) => {
  const [isUpdating, setIsUpdating] = useState(false);

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
        color: validated ? 'var(--color-green)' : 'var(--color-red)',
        fontSize: '1rem'
      }}>
        {validated ? '✓' : '✗'}
      </span>
    );
  };

  const updateHumanValidation = async () => {
    if (isUpdating) return;

    try {
      setIsUpdating(true);
      
      const updatedTag = {
        ...tag,
        human_validated: tag.human_validated === null ? true : !tag.human_validated
      };

      await postReceiptWordTag(updatedTag, word);

      if (onUpdateTag) {
        onUpdateTag(updatedTag);
      }
    } catch (error) {
      console.error('Failed to update human validation:', error);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div
      style={{
        cursor: 'pointer',
        padding: '4px',
        borderRadius: '2px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        position: 'relative',
        outline: isSelected ? '2px solid var(--color-blue)' : 'none'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', minWidth: '24px', justifyContent: 'center' }}>
        <div 
          onClick={(e) => {
            e.stopPropagation();
            updateHumanValidation();
          }}
          style={{ 
            display: 'flex', 
            alignItems: 'center',
            opacity: isUpdating ? 0.5 : 1,
            cursor: isUpdating ? 'not-allowed' : 'pointer'
          }}
        >
          {renderHumanValidation(tag.human_validated)}
        </div>
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
            color: tag.validated ? 'var(--color-green)' : 'var(--color-red)',
            border: `1px solid ${tag.validated ? 'var(--color-green)' : 'var(--color-red)'}`,
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '0.875rem',
            cursor: 'pointer'
          }}
        >
          {tag.tag.split('_').map(word => 
              word.charAt(0).toUpperCase() + word.slice(1)
            ).join(' ')}
        </span>
        {openTagMenu && (
          <TagMenu
            menuRef={menuRef}
            onSelect={(newTag) => {
              console.log('Single Update:', {
                selected_word: word,
                selected_new_tag: newTag,
                matching_tag: tag
              });
              onTagClick();
            }}
          />
        )}
      </div>
    </div>
  );
};

export default WordItem; 