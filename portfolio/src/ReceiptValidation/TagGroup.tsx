import React from 'react';
import { ReceiptWord, ReceiptWordTag } from '../interfaces';
import WordItem from './WordItem';

interface TagGroupProps {
  words: ReceiptWord[];
  tags: ReceiptWordTag[];
  tagType: string;
  selectedWord: ReceiptWord | null;
  onWordSelect: (word: ReceiptWord) => void;
  groupIndex: number;
  openTagMenu: { groupIndex: number; wordIndex: number; } | null;
  setOpenTagMenu: (value: { groupIndex: number; wordIndex: number; } | null) => void;
  menuRef: React.RefObject<HTMLDivElement>;
  isAddingTag: boolean;
  onAddTagClick: () => void;
  onUpdateTag: (updatedTag: ReceiptWordTag) => void;
  onRefresh: () => void;
}

const TagGroup: React.FC<TagGroupProps> = ({
  words,
  tags,
  tagType,
  selectedWord,
  onWordSelect,
  groupIndex,
  openTagMenu,
  setOpenTagMenu,
  menuRef,
  isAddingTag,
  onAddTagClick,
  onUpdateTag,
  onRefresh,
}) => {
  return (
    <div>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        marginBottom: '0.5rem',
        alignItems: 'center'
      }}>
        <div style={{
          fontSize: '1.5rem',
          color: 'var(--text-color)',
          position: 'relative',
          fontWeight: 'bold'
        }}>
          <div style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: -8,
            right: -8,
            border: isAddingTag ? '2px solid var(--color-yellow)' : 'none',
            borderRadius: '4px',
            zIndex: 0
          }} />
          <span style={{ position: 'relative', zIndex: 1 }}>
            {tagType.split('_').map(word => 
              word.charAt(0).toUpperCase() + word.slice(1)
            ).join(' ')}
          </span>
        </div>
        <div style={{
          width: '32px',
          height: '32px',
          borderRadius: '50%',
          backgroundColor: isAddingTag ? 'var(--color-yellow)' : 'var(--text-color)',
          color: 'var(--background-color)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          fontSize: '1.5rem',
          lineHeight: '1',
          paddingBottom: '3px',
          fontWeight: 'bold',
          userSelect: 'none'
        }} onClick={onAddTagClick}>
          +
        </div>
      </div>
      <div style={{
        padding: '8px',
        backgroundColor: 'var(--background-color)',
        border: '1px solid var(--text-color)',
        borderRadius: '4px',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        marginLeft: '6px',
      }}>
        {words.map((word, wordIdx) => {
          const wordTag = tags.find(t => 
            t.word_id === word.word_id && 
            t.line_id === word.line_id && 
            t.receipt_id === word.receipt_id &&
            t.image_id === word.image_id
          );

          if (!wordTag) {
            console.error('No matching tag found for word:', word);
            return null;
          }

          return (
            <WordItem
              key={`${word.image_id}-${word.line_id}-${word.word_id}`}
              word={word}
              tag={wordTag}
              isSelected={!!(selectedWord && 
                selectedWord.word_id === word.word_id &&
                selectedWord.line_id === word.line_id &&
                selectedWord.receipt_id === word.receipt_id &&
                selectedWord.image_id === word.image_id
              )}
              onWordClick={() => onWordSelect(word)}
              onTagClick={() => {
                setOpenTagMenu({
                  groupIndex,
                  wordIndex: wordIdx,
                });
              }}
              openTagMenu={openTagMenu?.groupIndex === groupIndex && openTagMenu?.wordIndex === wordIdx}
              menuRef={menuRef}
              onUpdateTag={onUpdateTag}
              onRefresh={onRefresh}
            />
          );
        })}
      </div>
    </div>
  );
};

export default TagGroup; 