import React from 'react';
import { ReceiptWord, ReceiptWordTag } from '../interfaces';
import WordItem from './WordItem';

interface TagGroupProps {
  words: ReceiptWord[];
  tag: ReceiptWordTag;
  tagType: string;
  selectedWord: ReceiptWord | null;
  onWordSelect: (word: ReceiptWord) => void;
  groupIndex: number;
  openTagMenu: { groupIndex: number; wordIndex: number; } | null;
  setOpenTagMenu: (value: { groupIndex: number; wordIndex: number; } | null) => void;
  menuRef: React.RefObject<HTMLDivElement>;
  isAddingTag: boolean;
  onAddTagClick: () => void;
}

const TagGroup: React.FC<TagGroupProps> = ({
  words,
  tag,
  tagType,
  selectedWord,
  onWordSelect,
  groupIndex,
  openTagMenu,
  setOpenTagMenu,
  menuRef,
  isAddingTag,
  onAddTagClick,
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
          fontSize: '2rem',
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
        gap: '4px'
      }}>
        {words.map((word, wordIdx) => (
          <WordItem
            key={wordIdx}
            word={word}
            tag={tag}
            isSelected={selectedWord?.word_id === word.word_id && 
                       selectedWord?.line_id === word.line_id && 
                       selectedWord?.receipt_id === word.receipt_id && 
                       selectedWord?.image_id === word.image_id}
            onWordClick={() => onWordSelect(word)}
            onTagClick={() => {
              setOpenTagMenu(
                openTagMenu?.groupIndex === groupIndex && 
                openTagMenu.wordIndex === wordIdx ? null : 
                { groupIndex, wordIndex: wordIdx }
              );
            }}
            openTagMenu={openTagMenu?.groupIndex === groupIndex && 
                        openTagMenu.wordIndex === wordIdx}
            menuRef={menuRef}
          />
        ))}
      </div>
    </div>
  );
};

export default TagGroup; 